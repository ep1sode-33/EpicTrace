from __future__ import annotations

import logging
import os

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
# 默认(folder_scan)collection 的 schema:仅含文件用得到的字段。session/timestamp/audio 等
# 留给 Plan 4(届时重建 collection + 重索引;向量可重建,代价可接受)。
_SCALARS = {
    "text": (DataType.VARCHAR, {"max_length": 65535}),
    "ingest_record_id": (DataType.INT64, {}),
    "project_id": (DataType.INT64, {}),
    "char_start": (DataType.INT64, {}),
    "char_end": (DataType.INT64, {}),
    "source_type": (DataType.VARCHAR, {"max_length": 64}),
    "embed_model_id": (DataType.VARCHAR, {"max_length": 128}),
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
                 scalars: dict | None = None) -> None:
        self._client = MilvusClient(db_path)
        self._dim = dim
        self._collection = collection
        self._scalars = scalars if scalars is not None else _SCALARS
        if not self._client.has_collection(collection):
            schema = self._client.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field("id", DataType.INT64, is_primary=True)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
            for name, (dtype, kw) in self._scalars.items():
                schema.add_field(name, dtype, **kw)
            index_params = self._client.prepare_index_params()
            index_params.add_index(
                field_name="vector", index_type="HNSW", metric_type="COSINE",
                params={"M": 16, "efConstruction": 200},
            )
            self._client.create_collection(collection, schema=schema, index_params=index_params)
        # 无论新建还是已存在,都确保 collection 已加载 —— 否则对"已存在(上次会话建的)
        # collection"调 search/query 会报 'collection is in state released'(对话检索走这条路,
        # app 重启后第一次提问必中)。load 对已加载的 collection 是幂等的。
        self._client.load_collection(collection)

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
