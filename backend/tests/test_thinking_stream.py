"""透明对话·阶段1:推理(reasoning)与正文分流 —— 推理走 thinking 事件、正文走 token,
落库 _answer 只含正文。老 provider(只有 stream)→ 无 thinking,行为不变(向后兼容)。"""
from epictrace.agent.answer import stream_final_answer
from epictrace.llm.openai_compat import OpenAICompatLLM
from epictrace.retrieval.types import RetrievedChunk


class _ReasoningLLM:
    """fake:stream_events 分离推理/正文;闸门用的 complete 返回 yes(可答)。"""
    def __init__(self, reasoning="我先想想", content="答案[1]"):
        self._r = reasoning
        self._c = content

    def complete(self, messages, **kw):
        return "yes"

    def stream_events(self, messages, **kw):
        for ch in self._r:
            yield {"type": "reasoning", "text": ch}
        for ch in self._c:
            yield {"type": "content", "text": ch}

    def stream(self, messages, **kw):
        for ch in self._c:
            yield ch


def _chunk():
    return RetrievedChunk(text="片段", ingest_record_id=1, project_id=1,
                          char_start=0, char_end=2, source_type="folder_scan")


def _run(llm, pool, q="问题"):
    think, toks, ans = [], [], None
    for ev in stream_final_answer(llm, q, pool, history=[], attached_names=[]):
        if ev["event"] == "thinking":
            think.append(ev["data"])
        elif ev["event"] == "token":
            toks.append(ev["data"])
        elif ev["event"] == "_answer":
            ans = ev["data"]
    return "".join(think), "".join(toks), ans


def test_reasoning_streamed_as_thinking_separate_from_answer():
    think, toks, ans = _run(_ReasoningLLM("我先想想这题", "答案是 X[1]"), [_chunk()])
    assert think == "我先想想这题"     # 推理 → thinking
    assert toks == "答案是 X[1]"        # 正文 → token
    assert ans == "答案是 X[1]"         # 落库只含正文(不混入推理)


def test_llm_without_stream_events_backward_compat():
    class _Plain:   # 只有 stream(老 provider / 测试 fake)
        def complete(self, m, **k):
            return "yes"

        def stream(self, m, **k):
            yield from "答案[1]"
    think, toks, ans = _run(_Plain(), [_chunk()])
    assert think == ""                  # 无推理事件
    assert toks == ans == "答案[1]"


class _FakeClient:
    """最小 OpenAI 形 client:chat.completions.create 返回预置 chunks。"""
    def __init__(self, chunks):
        self._chunks = chunks

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kw):
        return iter(self._chunks)


def _delta(r=None, c=None):
    class _D:
        reasoning_content = r
        content = c
    class _Ch:
        choices = [type("C", (), {"delta": _D()})()]
    return _Ch()


def test_openai_compat_stream_events_separates_and_stream_is_content_only():
    llm = OpenAICompatLLM.__new__(OpenAICompatLLM)   # 跳过真 client 构造
    llm._model = "x"
    chunks = [_delta(r="想"), _delta(r="法"), _delta(c="答"), _delta(c="案")]
    llm._client = _FakeClient(chunks)
    evs = list(llm.stream_events([{"role": "user", "content": "hi"}]))
    assert [e["type"] for e in evs] == ["reasoning", "reasoning", "content", "content"]
    # stream() 应只吐正文(向后兼容)
    llm._client = _FakeClient([_delta(r="想"), _delta(c="答"), _delta(c="案")])
    assert "".join(llm.stream([{"role": "user", "content": "hi"}])) == "答案"
