from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from epictrace.agent.react import FALLBACK, run_react_loop
from epictrace.agent.tools import ChunkAccumulator
from epictrace.retrieval.types import RetrievedChunk
from tests.fakes import FakeChatModel


def _proj_chunk(text="项目片段", rid=None, cs=0, ce=4):
    return RetrievedChunk(text=text, ingest_record_id=1, project_id=1,
                          char_start=cs, char_end=ce, source_type="folder_scan",
                          source_kind="project", reference_id=rid)


class _Retr:
    def __init__(self, out): self.out = out
    def retrieve(self, *, project_id, query, **kw): return list(self.out)


def _tools(retriever):
    from epictrace.agent.tools import build_tools
    return build_tools(retriever=retriever, project_id=1, focus_ids=[],
                       attachment_retriever=None, conversation_id=1,
                       indexed_ext_ids=[], reference_texts={})


def _call(name, args, cid="1"):
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


def test_single_round_then_answer_collects_pool():
    retr = _Retr([_proj_chunk("TLB项目内容")])
    model = FakeChatModel(script=[
        AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "TLB"})]),
        AIMessage(content="结束"),  # no tool_calls → exit loop
    ])
    acc = ChunkAccumulator()
    status = run_react_loop(model, _tools(retr), acc, "TLB是什么", history=[])
    assert status == "ok"
    assert [c.text for c in acc.chunks] == ["TLB项目内容"]


def test_multi_round_accumulates_across_rounds():
    retr = _Retr([_proj_chunk("片段A", cs=0, ce=3)])
    model = FakeChatModel(script=[
        AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "a"})]),
        AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "b"}, "2")]),
        AIMessage(content="够了"),
    ])
    acc = ChunkAccumulator()
    run_react_loop(model, _tools(retr), acc, "q", history=[])
    # same chunk both rounds → deduped to one
    assert len(acc.chunks) == 1


def test_parallel_tool_calls_in_one_round():
    retr = _Retr([_proj_chunk("X", cs=0, ce=1), _proj_chunk("Y", cs=1, ce=2)])
    model = FakeChatModel(script=[
        AIMessage(content="", tool_calls=[
            _call("search_project_library", {"query": "a"}, "1"),
            _call("search_project_library", {"query": "b"}, "2")]),
        AIMessage(content="done"),
    ])
    acc = ChunkAccumulator()
    run_react_loop(model, _tools(retr), acc, "q", history=[])
    assert {c.text for c in acc.chunks} == {"X", "Y"}


def test_round_cap_forces_answer_with_collected_pool():
    retr = _Retr([_proj_chunk("片段", cs=0, ce=2)])
    # model NEVER stops calling tools → must be capped
    never_stop = [AIMessage(content="", tool_calls=[
        _call("search_project_library", {"query": f"q{i}"}, str(i))]) for i in range(20)]
    model = FakeChatModel(script=never_stop)
    acc = ChunkAccumulator()
    status = run_react_loop(model, _tools(retr), acc, "q", history=[], max_rounds=8)
    assert status == "ok"
    assert len(model.invocations) <= 8     # round cap honored
    assert acc.chunks                       # pool non-empty → force-answer


def test_pool_capped_at_twelve():
    retr = _Retr([_proj_chunk(f"c{i}", cs=i, ce=i + 1) for i in range(30)])
    model = FakeChatModel(script=[
        AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "a"})]),
        AIMessage(content="done"),
    ])
    acc = ChunkAccumulator()
    run_react_loop(model, _tools(retr), acc, "q", history=[])
    assert len(acc.chunks) == 12


def test_empty_pool_no_tools_returns_direct():
    retr = _Retr([])
    model = FakeChatModel(script=[AIMessage(content="你好!")])  # greets, no tools
    acc = ChunkAccumulator()
    status = run_react_loop(model, _tools(retr), acc, "你好", history=[])
    assert status == "direct" and acc.chunks == []


def test_malformed_then_empty_pool_signals_fallback():
    retr = _Retr([])

    class _Boom:
        def __init__(self): self.n = 0
        def bind_tools(self, tools, **kw): return self
        def invoke(self, messages, **kw):
            self.n += 1
            raise RuntimeError("bad tool json")

    acc = ChunkAccumulator()
    status = run_react_loop(_Boom(), _tools(retr), acc, "q", history=[])
    assert status == FALLBACK     # first round crash + empty pool → fallback to Plan 5


def test_malformed_then_nonempty_pool_force_answers():
    retr = _Retr([_proj_chunk("已搜到")])

    class _OnceThenBoom:
        def __init__(self): self.n = 0
        def bind_tools(self, tools, **kw): self.tools = tools; return self
        def invoke(self, messages, **kw):
            self.n += 1
            if self.n == 1:
                return AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "a"})])
            raise RuntimeError("bad tool json")

    acc = ChunkAccumulator()
    status = run_react_loop(_OnceThenBoom(), _tools(retr), acc, "q", history=[])
    assert status == "ok" and acc.chunks      # pool has chunk → force-answer, not fallback
