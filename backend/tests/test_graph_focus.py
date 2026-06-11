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
