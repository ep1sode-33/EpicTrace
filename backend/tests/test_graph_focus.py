from epictrace.agent.graph import build_rag_graph
from epictrace.retrieval.types import RetrievedChunk
from tests.fakes import FakeLLM


class _SpyRetriever:
    def __init__(self): self.last_kwargs = None
    def retrieve(self, *, project_id, query, **kwargs):
        self.last_kwargs = kwargs
        return [RetrievedChunk(text="x", ingest_record_id=10, project_id=project_id,
                               char_start=0, char_end=1, source_type="folder_scan")]


def test_graph_passes_focus_ids_to_retriever():
    spy = _SpyRetriever()
    g = build_rag_graph(FakeLLM(route="retrieve", grade="sufficient"), spy)
    g.invoke({"project_id": 1, "question": "q", "query": "q", "history": [],
              "iterations": 0, "focus_ids": [10, 30]})
    assert spy.last_kwargs == {"ingest_record_ids": [10, 30]}


def test_graph_omits_kwarg_when_no_focus():
    spy = _SpyRetriever()
    g = build_rag_graph(FakeLLM(route="retrieve", grade="sufficient"), spy)
    g.invoke({"project_id": 1, "question": "q", "query": "q", "history": [],
              "iterations": 0, "focus_ids": []})
    assert spy.last_kwargs == {}


def test_focus_ids_force_retrieve_even_when_route_direct():
    spy = _SpyRetriever()
    g = build_rag_graph(FakeLLM(route="direct", grade="sufficient"), spy)
    g.invoke({"project_id": 1, "question": "hi", "query": "hi", "history": [],
              "iterations": 0, "focus_ids": [10]})
    # route=direct 本会跳过检索;但有 focus_ids → 仍必须检索(否则 pin 的内部文件被忽略)
    assert spy.last_kwargs == {"ingest_record_ids": [10]}


def test_direct_route_without_focus_still_skips_retrieve():
    spy = _SpyRetriever()
    g = build_rag_graph(FakeLLM(route="direct", grade="sufficient"), spy)
    g.invoke({"project_id": 1, "question": "hi", "query": "hi", "history": [],
              "iterations": 0, "focus_ids": []})
    assert spy.last_kwargs is None      # 无 focus + direct → retrieve 节点未运行


def test_graph_forces_retrieve_when_focus_even_if_route_direct():
    spy = _SpyRetriever()
    g = build_rag_graph(FakeLLM(route="direct", grade="sufficient"), spy)
    g.invoke({"project_id": 1, "question": "q", "query": "q", "history": [],
              "iterations": 0, "focus_ids": [10]})
    # 即便 route 判 direct,有聚焦内部引用就必须检索 → retriever 被调用且带 focus。
    assert spy.last_kwargs == {"ingest_record_ids": [10]}
