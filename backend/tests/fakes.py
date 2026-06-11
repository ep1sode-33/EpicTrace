from epictrace.interfaces.embedding import EmbeddingProvider
from epictrace.interfaces.vector_store import VectorStore
from epictrace.retrieval.types import RetrievedChunk


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


class FakeReranker:
    """按 query 子词在 chunk 文本里的命中次数打分;不依赖 torch。"""

    def warmup(self) -> None:
        return None

    def rerank(self, query: str, chunks: list[RetrievedChunk], top_k: int = 6) -> list[RetrievedChunk]:
        terms = [t for t in query.split() if t]

        def score(c: RetrievedChunk) -> int:
            return sum(c.text.count(t) for t in terms)

        return sorted(chunks, key=score, reverse=True)[:top_k]


class FakeLLM:
    """可编排:grade(固定)或 grade_sequence(逐次)、rewrite、answer。记录收到的 system 提示以分流。"""

    def __init__(self, *, grade="sufficient", grade_sequence=None, rewrite="改写后的查询", answer="假答案[1]"):
        self._grade = grade
        self._grade_seq = list(grade_sequence) if grade_sequence else None
        self._rewrite = rewrite
        self._answer = answer

    def _route(self, messages):
        sys = messages[0]["content"]
        if "sufficient" in sys:  # GRADE_SYS
            if self._grade_seq:
                return self._grade_seq.pop(0) if self._grade_seq else "sufficient"
            return self._grade
        if "改写" in sys:  # REWRITE_SYS
            return self._rewrite
        return self._answer  # GENERATE_SYS

    def complete(self, messages, **kwargs):
        return self._route(messages)

    def stream(self, messages, **kwargs):
        for ch in self._route(messages):
            yield ch
