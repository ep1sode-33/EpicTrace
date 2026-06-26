"""#144 接地闸门:检索后、生成前判【资料】是否真含答案;不含 → 生成自然接地的拒答(不编造)。
保守偏可答:只有明确 no 才拒;判失败/池空 → 放行正常生成。"""
import json

from epictrace.agent.answer import REFUSAL_SYS, stream_final_answer
from epictrace.retrieval.types import RetrievedChunk


class _GateLLM:
    """fake:complete() 返回可编排的可答判定(yes/no);stream() 按系统提示分流——
    REFUSAL_SYS → 吐拒答串,否则吐普通答案串。"""
    def __init__(self, answerable="yes", answer="正常答案[1]",
                 refusal="资料里没有涉及这个,无法据此回答。"):
        self._answerable = answerable
        self._answer = answer
        self._refusal = refusal
        self.completed: list = []
        self.streamed: list = []

    def complete(self, messages, **kw):
        self.completed.append(list(messages))
        return self._answerable

    def stream(self, messages, **kw):
        self.streamed.append(list(messages))
        text = self._refusal if messages[0]["content"] == REFUSAL_SYS else self._answer
        for ch in text:
            yield ch


def _chunk(text="项目片段", rid=1):
    return RetrievedChunk(text=text, ingest_record_id=rid, project_id=1,
                          char_start=0, char_end=len(text), source_type="folder_scan")


def _run(llm, pool, question="问题"):
    toks: list[str] = []
    cites = None
    ans = None
    for ev in stream_final_answer(llm, question, pool, history=[], attached_names=[]):
        if ev["event"] == "token":
            toks.append(ev["data"])
        elif ev["event"] == "citations":
            cites = json.loads(ev["data"])
        elif ev["event"] == "_answer":
            ans = ev["data"]
    return "".join(toks), cites, ans


def test_unanswerable_pool_generates_natural_refusal_not_fabrication():
    # gate 判 no → 走 REFUSAL_SYS 生成自然拒答:不编造、无引用,且不是僵硬模板(是生成的)。
    llm = _GateLLM(answerable="no", answer="瞎编的价格是 $0.28[1]")
    answer, cites, ans = _run(llm, [_chunk()])
    assert ("无法" in answer) or ("没有" in answer)   # 是拒答
    assert "0.28" not in answer                        # 没走生成、没编造
    assert cites == []                                  # 拒答不带引用
    assert ans == answer
    assert any(m[0]["content"] == REFUSAL_SYS for m in llm.streamed)   # 走的是拒答生成,非模板


def test_answerable_pool_generates_normally():
    llm = _GateLLM(answerable="yes", answer="正常答案[1]")
    answer, cites, _ = _run(llm, [_chunk()])
    assert answer == "正常答案[1]"
    assert [c["n"] for c in cites] == [1]
    assert all(m[0]["content"] != REFUSAL_SYS for m in llm.streamed)   # 走正常生成


def test_gate_no_with_trailing_text_still_refuses():
    # 模型多嘴回 "No, 资料里没有" → 仍判不可答 → 生成拒答。
    llm = _GateLLM(answerable="No, 资料里没有相关信息", answer="瞎编[1]")
    answer, _, _ = _run(llm, [_chunk()])
    assert ("无法" in answer) or ("没有" in answer)
    assert "瞎编" not in answer


def test_gate_failure_is_conservative_answers():
    # complete 抛错 → 保守放行(当可答),正常生成,绝不误拒。
    class _Boom(_GateLLM):
        def complete(self, messages, **kw):
            raise RuntimeError("gate boom")
    llm = _Boom(answer="正常答案[1]")
    answer, _, _ = _run(llm, [_chunk()])
    assert answer == "正常答案[1]"


def test_empty_pool_skips_gate_direct():
    # 空池(寒暄)不触发 gate,走 direct;complete 不应被调用。
    llm = _GateLLM(answerable="no", answer="你好,有什么可以帮你?")
    answer, cites, _ = _run(llm, [], question="你好")
    assert answer == "你好,有什么可以帮你?"
    assert cites == []
    assert llm.completed == []      # 空池没调 gate
