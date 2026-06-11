from __future__ import annotations

import logging

from pymilvus import DataType, MilvusClient

from epictrace.interfaces.vector_store import VectorStore

_log = logging.getLogger("epictrace")

_COLLECTION = "chunks"
# Milvus query 的硬上限:单次 query 最多返回这么多行。list_by_project 用它一次性拉全项目
# 的 chunk 喂给 BM25(稀疏检索语料)。超过此数会被静默截断 → BM25 语料不全、稀疏召回有缺口。
_LIST_LIMIT = 16384
# 本期 schema:仅含 folder_scan 文件用得到的字段。session/timestamp/audio 等留给 Plan 4
# (届时重建 collection + 重索引;向量可重建,代价可接受)。
_SCALARS = {
    "text": (DataType.VARCHAR, {"max_length": 65535}),
    "ingest_record_id": (DataType.INT64, {}),
    "project_id": (DataType.INT64, {}),
    "char_start": (DataType.INT64, {}),
    "char_end": (DataType.INT64, {}),
    "source_type": (DataType.VARCHAR, {"max_length": 64}),
    "embed_model_id": (DataType.VARCHAR, {"max_length": 128}),
}


class MilvusLiteStore(VectorStore):
    def __init__(self, db_path: str, dim: int = 1024) -> None:
        self._client = MilvusClient(db_path)
        self._dim = dim
        if not self._client.has_collection(_COLLECTION):
            schema = self._client.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field("id", DataType.INT64, is_primary=True)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
            for name, (dtype, kw) in _SCALARS.items():
                schema.add_field(name, dtype, **kw)
            index_params = self._client.prepare_index_params()
            index_params.add_index(
                field_name="vector", index_type="HNSW", metric_type="COSINE",
                params={"M": 16, "efConstruction": 200},
            )
            self._client.create_collection(
                _COLLECTION, schema=schema, index_params=index_params
            )
            self._client.load_collection(_COLLECTION)

    def upsert(self, records: list[dict]) -> None:
        if not records:
            return
        self._client.insert(_COLLECTION, records)

    def query(self, vector: list[float], filter: dict | None, k: int) -> list[dict]:
        expr = None
        if filter:
            expr = " and ".join(f"{key} == {val!r}" if isinstance(val, str)
                                else f"{key} == {val}" for key, val in filter.items())
        res = self._client.search(
            _COLLECTION, data=[vector], limit=k, filter=expr or "",
            output_fields=list(_SCALARS.keys()),
        )
        return [hit["entity"] for hit in res[0]]

    def delete_by_record(self, ingest_record_id: int) -> None:
        self._client.delete(_COLLECTION, filter=f"ingest_record_id == {ingest_record_id}")

    def delete_by_project(self, project_id: int) -> None:
        self._client.delete(_COLLECTION, filter=f"project_id == {project_id}")

    def list_by_project(self, project_id: int) -> list[dict]:
        # limit=_LIST_LIMIT 是 Milvus 的硬上限;命中上限即很可能被截断(BM25 语料不完整)。
        # TODO(后续优化):大项目按主键分页全量拉取,并对 BM25 语料做进程内缓存/增量更新,
        #   避免每次稀疏检索都全量 query + 重建 BM25。本期先告警暴露问题,不做缓存。
        rows = self._client.query(
            _COLLECTION,
            filter=f"project_id == {project_id}",
            output_fields=list(_SCALARS.keys()),
            limit=_LIST_LIMIT,
        )
        if len(rows) == _LIST_LIMIT:
            _log.warning(
                "list_by_project(project_id=%s) 命中 %d 行上限,可能被截断 → BM25 稀疏检索语料不完整。",
                project_id, _LIST_LIMIT,
            )
        return rows
