from __future__ import annotations

import logging
import os
from typing import Callable

# Milvus 用 gRPC。在 embedder/reranker(多进程)fork 之后再构造 gRPC client,会在 macOS 段错误
# (crash 在 cygrpc pollset_work)。gRPC 官方 fork 支持开关必须在 import pymilvus(→gRPC 初始化)
# 之前设;setdefault 保留外部覆盖。修本会话 eval retrieve 的稳定段错误(见 macos-embedding-milvus-fork)。
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "1")

from pymilvus import DataType, MilvusClient  # noqa: E402 — 须在上面设完 env 后再导入

from epictrace.interfaces.vector_store import VectorStore  # noqa: E402

_log = logging.getLogger("epictrace")

_COLLECTION = "chunks"
# Milvus query 的硬上限:单次 query 最多返回这么多行。list_by 用它一次性拉全(如全项目的 chunk
# 喂给 BM25 稀疏检索语料)。超过此数会被静默截断 → 语料不全、稀疏召回有缺口。
_LIST_LIMIT = 16384
# 向量索引用 FLAT(精确暴力检索),不用 HNSW —— 这是修复,不是偷懒:
# milvus-lite 的 HNSW 检索是"全局 beam 遍历(ef 固定 64,search_params 传不进去)+ 拿 filter
# 当结果 mask",等价于先取全局 ~一两百条候选再按 project 后过滤。多 project 同库时,冷门
# project 的有效 dense 深度只剩个位~几十条(dense_n 形同虚设),且随其他 project 语料增长
# 静默劣化 —— eval 实锤过 3 个池级漏检。FLAT 走 BruteForceIndex:filter 先变 valid_mask、
# 只对过滤后的行精确算距离,filtered KNN 语义完整;顺带把 faiss 踢出检索路径(faiss 与 torch
# 撞双 libomp 的老坑)。代价是 O(N·dim)/查询,本地单机万级 chunk 是毫秒量级,与稀疏通道每查
# 全量拉语料重建 BM25 同一个量级。规模真上去了按 VectorStore 接口缝换真 filtered-ANN 实现。
_INDEX_TYPE = "FLAT"
# 默认(folder_scan)collection 的 schema,v2:capture_session_id/ts 让 chunk 可回溯到
# 会话时刻(哨兵 0/"" = 非会话来源;ts 为 naive-UTC ISO 秒级,与 timealign marker 同源)。
# 升级后旧 collection 由 __init__ 的字段集比对自愈重建;记录侧的一次性重置见
# services.index.reset_index_if_schema_upgraded(把全库翻回待索引,用户手动重建)。
_SCALARS = {
    "text": (DataType.VARCHAR, {"max_length": 65535}),
    "ingest_record_id": (DataType.INT64, {}),
    "project_id": (DataType.INT64, {}),
    "char_start": (DataType.INT64, {}),
    "char_end": (DataType.INT64, {}),
    "source_type": (DataType.VARCHAR, {"max_length": 64}),
    "embed_model_id": (DataType.VARCHAR, {"max_length": 128}),
    "capture_session_id": (DataType.INT64, {}),
    "ts": (DataType.VARCHAR, {"max_length": 64}),
}
# 临时附件(session attachment)collection 的 schema:用 session_id/reference_id 取代
# project_id/ingest_record_id —— 这是会话级临时 RAG,随会话清理,不进用户的 Project 文件夹。
# 旧键 conversation_id 已随对话栈迁移废弃;字段集不一致时由 __init__ 自愈 drop 重建(旧向量是孤儿)。
_ATTACHMENT_SCALARS = {
    "text": (DataType.VARCHAR, {"max_length": 65535}),
    "session_id": (DataType.INT64, {}),
    "reference_id": (DataType.INT64, {}),
    "char_start": (DataType.INT64, {}),
    "char_end": (DataType.INT64, {}),
    "source_type": (DataType.VARCHAR, {"max_length": 64}),
    "embed_model_id": (DataType.VARCHAR, {"max_length": 128}),
}


class MilvusLiteStore(VectorStore):
    def __init__(self, db_path: str, dim: int = 1024, collection: str = _COLLECTION,
                 scalars: dict | None = None,
                 on_schema_heal: Callable[[], None] | None = None) -> None:
        # on_schema_heal:仅在下面「字段集不一致 → drop 重建」的自愈分支发生后调用,
        # 让宿主把 SQLite 里的 IngestRecord.indexed 翻回 False —— 否则 collection 被 drop
        # 后向量为空、而记录仍 indexed=True,常规索引零目标、检索静默变空(F3)。
        self._client = MilvusClient(db_path)
        self._dim = dim
        self._collection = collection
        self._scalars = scalars if scalars is not None else _SCALARS
        # schema 自愈:已存在的 collection 若字段集与当前 schema 不一致(如 v1 库缺
        # capture_session_id/ts),drop 后落入下面的 create 分支按新 schema 重建。
        # 向量是派生索引,可由重索引恢复;scalars 未变的 collection(如 attachment)
        # 字段集一致,天然 no-op。
        if self._client.has_collection(collection):
            desc = self._client.describe_collection(collection)
            existing = {f["name"] for f in desc["fields"]}
            expected = {"id", "vector", *self._scalars}
            # 向量维度也要比对(codex review R2:改 dimensions 后旧宽度 collection 必须重建,
            # 否则查询/写入全报维度错,且绕过 on_schema_heal 的 indexed 重置)
            vec_field = next((f for f in desc["fields"] if f["name"] == "vector"), {})
            existing_dim = (vec_field.get("params") or {}).get("dim")
            if existing != expected or (existing_dim is not None and int(existing_dim) != self._dim):
                _log.warning(
                    "collection %s schema 不一致(字段集 %s vs %s,维度 %s vs %s),"
                    "drop 后重建(向量可由重索引恢复)",
                    collection, sorted(existing), sorted(expected), existing_dim, self._dim,
                )
                # 必须先 release 再 drop:drop 内部的 close 会把 WAL 回放的 memtable
                # flush 成新 segment 并排队构建 faiss 索引 —— 本进程若已加载 torch
                # (app 的 warmup-first 顺序与 pytest 都如此),faiss 首次并行 add 会撞
                # 双 libomp(OMP Error #15)直接 abort 整个进程。released 状态让排队的
                # 索引构建自然跳过:不为一个马上要删掉的 collection 冒进程级风险。
                self._client.release_collection(collection)
                self._client.drop_collection(collection)
                # 自愈 drop 后回调宿主(重置 indexed);回调失败只记 warning,不挡 store 构造。
                if on_schema_heal is not None:
                    try:
                        on_schema_heal()
                    except Exception as e:  # noqa: BLE001 — 回调是宿主职责,失败不该炸掉 store 构造
                        _log.warning("schema 自愈回调失败(不阻断 store 构造): %s", e)
        if not self._client.has_collection(collection):
            schema = self._client.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field("id", DataType.INT64, is_primary=True)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
            for name, (dtype, kw) in self._scalars.items():
                schema.add_field(name, dtype, **kw)
            index_params = self._client.prepare_index_params()
            index_params.add_index(
                field_name="vector", index_type=_INDEX_TYPE, metric_type="COSINE",
            )
            self._client.create_collection(collection, schema=schema, index_params=index_params)
        else:
            # 存量库(旧版建的 HNSW 索引)就地换 FLAT:索引是数据(parquet)的派生物,
            # drop/create 只改索引 spec,行与向量原样保留 —— 不需要重新 embedding。
            self._heal_index_type()
        # 无论新建还是已存在,都确保 collection 已加载 —— 否则对"已存在(上次会话建的)
        # collection"调 search/query 会报 'collection is in state released'(对话检索走这条路,
        # app 重启后第一次提问必中)。load 对已加载的 collection 是幂等的。
        #
        # load 同时是索引文件"物理可加载性"的唯一关卡:describe_index 只读 manifest 元数据,
        # 看不出半写/损坏的索引 sidecar(引擎 create_index 先提交 manifest spec、load 时才
        # 非原子地写 .idx 文件 —— 进程死在写入中途就留下这个状态),而引擎 load 读到损坏文件
        # 直接抛错且不清理 → 不兜底则每次重开都在同一处失败,向量库持续不可用(故障注入实锤,
        # 行数据 parquet 完好)。兜底 = 走公开 API 重建派生索引:drop_index 连带删掉该索引的
        # 引擎持久化文件,重新 load 时从行数据重建 —— 一行不丢、无需重新 embedding,也不触发
        # on_schema_heal。只重试一次:重建后仍失败说明不是索引层的问题(如数据文件损坏),
        # 原样抛出,不掩盖。对没有"索引 sidecar"概念的未来引擎安全降级:load 不失败就永远
        # 不进这条路,进了也只用公开索引 API。
        try:
            self._client.load_collection(collection)
        except Exception as e:  # noqa: BLE001 — 引擎侧抛 MilvusException,兜底不赌具体异常类型
            _log.warning(
                "collection %s load 失败(疑似向量索引文件损坏),drop 索引清理后重建重试:%s",
                collection, e,
            )
            self._rebuild_vector_index()
            self._client.load_collection(collection)

    def _heal_index_type(self) -> None:
        """向量索引类型自愈:已存在的 collection 若 vector 字段索引不是 FLAT(旧版建的
        HNSW),release → drop_index → create_index(FLAT)。见 _INDEX_TYPE 注释:HNSW 在
        milvus-lite 下 filter 是全局候选后过滤,project 隔离失效。

        引擎实际行为(源码核实,注意 create_index 的引擎 docstring 是过时的):
        - drop_index **当场删除**旧 .hnsw.idx 文件,并在无剩余索引时把 load-state
          自动翻回 loaded;
        - create_index 在 loaded 状态下**同步内联**为现有 segment 构建 FLAT 索引并写
          .idx sidecar —— 本方法返回时检索即已可用、即已精确,不依赖下次 flush。
        数据(parquet)与向量原样保留,不需重新 embedding;不触发 on_schema_heal:
        数据一行未动,无需把记录翻回待索引。"""
        names = self._client.list_indexes(self._collection)
        # 只看/只动 vector 字段的索引:当前引擎每 collection 只有单索引,这里是防御 ——
        # 万一未来加了 scalar/sparse 索引,不许被这条自愈连带删掉。
        infos = {n: self._client.describe_index(self._collection, n) for n in names}
        vector_idx = {n: i for n, i in infos.items() if i.get("field_name") == "vector"}
        stale = [n for n, i in vector_idx.items() if i.get("index_type") != _INDEX_TYPE]
        if vector_idx and not stale:
            return
        _log.warning(
            "collection %s vector 索引 %s 非 %s,就地换索引(数据/向量保留,无需重索引)",
            self._collection, stale or "缺失", _INDEX_TYPE,
        )
        self._rebuild_vector_index()

    def _rebuild_vector_index(self) -> None:
        """重建 vector 字段的索引 spec:release → drop 全部 vector 索引(不论类型)→
        建 FLAT。两处调用:索引类型自愈(HNSW→FLAT 迁移)与 __init__ 的 load 失败兜底
        (损坏 sidecar 清理)。drop_index 的公开语义包含删除该索引的引擎持久化文件
        (.idx sidecar),所以这同时就是损坏文件的清理路径 —— 全程公开 API,不碰引擎
        私有文件布局。幂等:成功后再重开,describe 是 FLAT、load 直接成功,两个调用方
        都不会再进来;中途再死一次,下次重开还是同样的单次重建。

        与 schema 自愈同理先 release:drop_index 拒绝在 loaded collection 上执行;
        此处要么尚未 load、要么 load 刚失败被引擎回滚到 released,release 对未加载的
        collection 是安全的 no-op。只动 vector 字段的索引(引擎每字段至多一个索引):
        万一未来加了 scalar/sparse 索引,不许被这条重建连带删掉。"""
        self._client.release_collection(self._collection)
        for n in self._client.list_indexes(self._collection):
            if self._client.describe_index(self._collection, n).get("field_name") == "vector":
                self._client.drop_index(self._collection, n)
        index_params = self._client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type=_INDEX_TYPE, metric_type="COSINE")
        self._client.create_index(self._collection, index_params)

    def close(self) -> None:
        """释放 milvus-lite 的独占文件锁(便于同进程内重开 store / 测试模拟重启)。"""
        self._client.close()

    def upsert(self, records: list[dict]) -> None:
        if not records:
            return
        self._client.insert(self._collection, records)

    def query(self, vector: list[float], filter: dict | None, k: int) -> list[dict]:
        expr = self._build_expr(filter)
        res = self._client.search(
            self._collection, data=[vector], limit=k, filter=expr or "",
            output_fields=list(self._scalars.keys()),
        )
        return [hit["entity"] for hit in res[0]]

    @staticmethod
    def _build_expr(filter: dict | None) -> str | None:
        if not filter:
            return None
        parts = []
        for key, val in filter.items():
            if isinstance(val, (list, tuple)):
                parts.append(f"{key} in {list(val)}")
            elif isinstance(val, str):
                parts.append(f"{key} == {val!r}")
            else:
                parts.append(f"{key} == {val}")
        return " and ".join(parts)

    def list_by(self, filter: dict) -> list[dict]:
        # limit=_LIST_LIMIT 是 Milvus 的硬上限;命中上限即很可能被截断(如 BM25 语料不完整)。
        # TODO(后续优化):大集合按主键分页全量拉取,并对 BM25 语料做进程内缓存/增量更新,
        #   避免每次稀疏检索都全量 query + 重建 BM25。本期先告警暴露问题,不做缓存。
        rows = self._client.query(
            self._collection, filter=self._build_expr(filter) or "",
            output_fields=list(self._scalars.keys()), limit=_LIST_LIMIT,
        )
        if len(rows) == _LIST_LIMIT:
            _log.warning("list_by(%s) 命中 %d 行上限,可能被截断。", filter, _LIST_LIMIT)
        return rows

    def delete(self, filter: dict) -> None:
        self._client.delete(self._collection, filter=self._build_expr(filter) or "")

    def delete_by_record(self, ingest_record_id: int) -> None:
        self.delete({"ingest_record_id": ingest_record_id})

    def delete_by_project(self, project_id: int) -> None:
        self.delete({"project_id": project_id})

    def list_by_project(self, project_id: int) -> list[dict]:
        return self.list_by({"project_id": project_id})
