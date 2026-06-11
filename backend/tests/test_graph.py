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
    # 首次检索为空 → grade 短路判 insufficient(不消耗 grade_sequence),改写后第二次检到资料
    # → 此时才真正问 LLM,返回 sufficient 收尾。
    retr = _Retriever({"页表是什么": [], "页表 虚拟内存 分页": _chunks("页表与分页")})
    llm = FakeLLM(grade="sufficient", rewrite="页表 虚拟内存 分页")
    graph = build_rag_graph(llm, retr)
    out = graph.invoke({"project_id": 7, "question": "页表是什么", "query": "页表是什么",
                        "history": [], "iterations": 0})
    assert retr.calls == ["页表是什么", "页表 虚拟内存 分页"]
    assert out["chunks"][0].text == "页表与分页"


def test_empty_chunks_with_garbage_grade_never_exits_sufficient():
    # 永远检不到 → grade 始终短路 insufficient;即便 LLM 在(理论上)被问到时回垃圾,
    # 也绝不会判 sufficient。只能靠迭代上限收尾,且终态 chunks 为空(零资料不假装充分)。
    retr = _Retriever({})
    graph = build_rag_graph(FakeLLM(grade="马马虎虎也许吧", rewrite="x"), retr, max_iterations=2)
    out = graph.invoke({"project_id": 7, "question": "q", "query": "q", "history": [], "iterations": 0})
    assert out["chunks"] == []                         # 零资料,绝不臆造充分
    assert len(retr.calls) == 3                         # 初次 + 2 次改写后到上限停


def test_garbage_grade_on_nonempty_chunks_is_insufficient():
    # 有资料但 grade 回含糊/垃圾(既非明确 sufficient)→ 严格解析按 insufficient → 继续改写到上限。
    retr = _Retriever({"q": _chunks("一些资料")})
    graph = build_rag_graph(FakeLLM(grade="可能 sufficient 但也 insufficient", rewrite="q"),
                            retr, max_iterations=2)
    out = graph.invoke({"project_id": 7, "question": "q", "query": "q", "history": [], "iterations": 0})
    assert out["chunks"][0].text == "一些资料"         # 到上限兜底,仍带回已检到的资料
    assert len(retr.calls) == 3


def test_iteration_cap_stops_retrying():
    retr = _Retriever({})  # 永远检不到
    graph = build_rag_graph(FakeLLM(grade="insufficient", rewrite="x"), retr, max_iterations=2)
    out = graph.invoke({"project_id": 7, "question": "q", "query": "q", "history": [], "iterations": 0})
    assert len(retr.calls) <= 3                       # 初次 + 最多 2 次改写后停


def test_direct_route_skips_retrieval_and_ends_with_no_chunks():
    # route="direct"(打招呼/常识)→ 完全不检索,终态无 chunk。
    retr = _Retriever({"你好": _chunks("不该被检到")})
    graph = build_rag_graph(FakeLLM(route="direct"), retr)
    out = graph.invoke({"project_id": 7, "question": "你好", "query": "你好",
                        "history": [], "iterations": 0})
    assert retr.calls == []                            # 0 次检索
    assert out.get("chunks", []) == []                 # 直答路径无资料
    assert out["route"] == "direct"


def test_retrieve_route_runs_existing_flow():
    # route="retrieve" → 走原检索环(检索 + grade 判 sufficient 收尾)。
    retr = _Retriever({"页表是什么": _chunks("页表映射地址")})
    graph = build_rag_graph(FakeLLM(route="retrieve", grade="sufficient"), retr)
    out = graph.invoke({"project_id": 7, "question": "页表是什么", "query": "页表是什么",
                        "history": [], "iterations": 0})
    assert len(retr.calls) == 1
    assert out["chunks"][0].text == "页表映射地址"
    assert out["route"] == "retrieve"


def test_ambiguous_route_defaults_to_retrieve():
    # 含糊/垃圾路由结果 → 保守接地,按 retrieve 处理。
    retr = _Retriever({"q": _chunks("一些资料")})
    graph = build_rag_graph(FakeLLM(route="也许吧 retrieve 或 direct", grade="sufficient"), retr)
    out = graph.invoke({"project_id": 7, "question": "q", "query": "q", "history": [], "iterations": 0})
    assert len(retr.calls) == 1
    assert out["route"] == "retrieve"
