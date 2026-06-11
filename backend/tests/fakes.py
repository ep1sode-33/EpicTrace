from epictrace.interfaces.embedding import EmbeddingProvider
from epictrace.interfaces.vector_store import VectorStore


class FakeVectorStore(VectorStore):
    """记录调用,便于断言项目删除时向量被清理;不依赖 Milvus。"""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.deleted_projects: list[int] = []
        self.deleted_records: list[int] = []

    def upsert(self, records: list[dict]) -> None:
        self.records.extend(records)

    def query(self, vector: list[float], filter: dict | None, k: int) -> list[dict]:
        rows = self.records
        if filter:
            rows = [r for r in rows if all(r.get(key) == val for key, val in filter.items())]
        return rows[:k]

    def delete_by_record(self, ingest_record_id: int) -> None:
        self.deleted_records.append(ingest_record_id)
        self.records = [r for r in self.records if r.get("ingest_record_id") != ingest_record_id]

    def delete_by_project(self, project_id: int) -> None:
        self.deleted_projects.append(project_id)
        self.records = [r for r in self.records if r.get("project_id") != project_id]

    def list_by_project(self, project_id: int) -> list[dict]:
        return [r for r in self.records if r.get("project_id") == project_id]


class FakeEmbedder(EmbeddingProvider):
    """确定性 1024 维向量,遵守 EmbeddingProvider 契约;不依赖 torch。"""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = (sum(ord(c) for c in t) % 97) / 97.0
            out.append([h] * self._dim)
        return out

    @property
    def model_id(self) -> str:
        return "fake"
