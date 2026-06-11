from epictrace.agent.graph import build_rag_graph
from epictrace.retrieval.types import RetrievedChunk
from tests.fakes import FakeLLM


def _chunks(*texts):
    return [RetrievedChunk(text=t, ingest_record_id=i + 1, project_id=7, char_start=0,
                           char_end=len(t), source_type="folder_scan") for i, t in enumerate(texts)]


class _Retriever:
    def __init__(self, by_query): self.by_query = by_query; self.calls = []
    def retrieve(self, *, project_id, query, k=6):
        self.calls.append(query)
        return self.by_query.get(query, [])


def test_sufficient_retrieval_ends_after_one_retrieve():
    retr = _Retriever({"页表是什么": _chunks("页表映射地址")})
    graph = build_rag_graph(FakeLLM(grade="sufficient"), retr)
    out = graph.invoke({"project_id": 7, "question": "页表是什么", "query": "页表是什么",
                        "history": [], "iterations": 0})
    assert len(retr.calls) == 1                       # 足够 → 不改写
    assert out["chunks"][0].text == "页表映射地址"


def test_insufficient_triggers_rewrite_then_retrieves_again():
    retr = _Retriever({"页表是什么": [], "页表 虚拟内存 分页": _chunks("页表与分页")})
    llm = FakeLLM(grade_sequence=["insufficient", "sufficient"], rewrite="页表 虚拟内存 分页")
    graph = build_rag_graph(llm, retr)
    out = graph.invoke({"project_id": 7, "question": "页表是什么", "query": "页表是什么",
                        "history": [], "iterations": 0})
    assert retr.calls == ["页表是什么", "页表 虚拟内存 分页"]
    assert out["chunks"][0].text == "页表与分页"


def test_iteration_cap_stops_retrying():
    retr = _Retriever({})  # 永远检不到
    graph = build_rag_graph(FakeLLM(grade="insufficient", rewrite="x"), retr, max_iterations=2)
    out = graph.invoke({"project_id": 7, "question": "q", "query": "q", "history": [], "iterations": 0})
    assert len(retr.calls) <= 3                       # 初次 + 最多 2 次改写后停
