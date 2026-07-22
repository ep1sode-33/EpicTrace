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

    def _match(self, r: dict, filter: dict) -> bool:
        for key, val in filter.items():
            rv = r.get(key)
            if isinstance(val, (list, tuple)):
                if rv not in val:
                    return False
            elif rv != val:
                return False
        return True

    def query(self, vector, filter, k):
        rows = self.records if not filter else [r for r in self.records if self._match(r, filter)]
        return rows[:k]

    def list_by(self, filter: dict) -> list[dict]:
        return [r for r in self.records if self._match(r, filter)]

    def delete(self, filter: dict) -> None:
        self.records = [r for r in self.records if not self._match(r, filter)]

    def delete_by_record(self, ingest_record_id: int) -> None:
        self.deleted_records.append(ingest_record_id)
        self.delete({"ingest_record_id": ingest_record_id})

    def delete_by_project(self, project_id: int) -> None:
        self.deleted_projects.append(project_id)
        self.delete({"project_id": project_id})

    def list_by_project(self, project_id: int) -> list[dict]:
        return self.list_by({"project_id": project_id})


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


class FakeCoworkComplete:
    """cowork AgentLoop 的 complete_fn 假件:按脚本依次返回 LLMResponse,脚本耗尽回默认。

    记录每次调用收到的 (messages, tools),供断言工具 schema 与历史回放。"""

    def __init__(self, script=None, default=None):
        from epictrace.cowork.llm_client import LLMResponse

        self._script = list(script or [])
        self._default = default or LLMResponse(content="假答案")
        self.calls: list[tuple[list[dict], list[dict]]] = []

    def __call__(self, messages, tools):
        self.calls.append((list(messages), list(tools)))
        if self._script:
            return self._script.pop(0)
        return self._default
