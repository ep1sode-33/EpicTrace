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
# 临时附件(chat attachment)collection 的 schema:用 conversation_id/reference_id 取代
# project_id/ingest_record_id —— 这是会话级临时 RAG,随会话清理,不进用户的 Project 文件夹。
_ATTACHMENT_SCALARS = {
    "text": (DataType.VARCHAR, {"max_length": 65535}),
    "conversation_id": (DataType.INT64, {}),
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
            existing = {f["name"] for f in self._client.describe_collection(collection)["fields"]}
            expected = {"id", "vector", *self._scalars}
            if existing != expected:
                _log.warning(
                    "collection %s 字段集 %s 与当前 schema %s 不一致,drop 后重建(向量可由重索引恢复)",
                    collection, sorted(existing), sorted(expected),
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
        self._client.load_collection(collection)

    def _heal_index_type(self) -> None:
        """向量索引类型自愈:已存在的 collection 若索引不是 FLAT(旧版建的 HNSW),
        release → drop_index → create_index(FLAT)。见 _INDEX_TYPE 注释:HNSW 在 milvus-lite
        下 filter 是全局候选后过滤,project 隔离失效。swap 后旧 .idx 文件因命名含索引类型
        不再被挂载,查询立即走 brute-force 精确路径;新 FLAT 索引由引擎在下次 flush 后台重建。
        不触发 on_schema_heal:数据一行未动,无需把记录翻回待索引。"""
        names = self._client.list_indexes(self._collection)
        stale = [n for n in names
                 if self._client.describe_index(self._collection, n).get("index_type") != _INDEX_TYPE]
        if names and not stale:
            return
        _log.warning(
            "collection %s 向量索引 %s 非 %s,就地换索引(数据/向量保留,无需重索引)",
            self._collection, names, _INDEX_TYPE,
        )
        # 与 schema 自愈同理先 release:drop_index 拒绝在 loaded collection 上执行,
        # 且此时尚未 load,release 对未加载的 collection 是安全的 no-op。
        self._client.release_collection(self._collection)
        for n in names:
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
