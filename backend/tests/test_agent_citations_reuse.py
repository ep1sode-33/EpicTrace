import json

from epictrace.agent.answer import stream_final_answer
from epictrace.retrieval.types import RetrievedChunk


class _StreamLLM:
    """OpenAICompatLLM-shaped fake: streams a fixed answer, records messages."""
    def __init__(self, answer): self.answer = answer; self.messages = None
    def stream(self, messages, **kw):
        self.messages = list(messages)
        for ch in self.answer:
            yield ch


def _proj():
    return RetrievedChunk(text="项目片段TLB", ingest_record_id=99, project_id=1,
                          char_start=0, char_end=6, source_type="folder_scan")


def _attach():
    return RetrievedChunk(text="附件页表内容", ingest_record_id=0, project_id=0,
                          char_start=5, char_end=11, source_type="attachment",
                          source_kind="attachment", reference_id=42)


def _run(llm, pool, question="问题", history=None, attached_names=None):
    toks, cites = [], None
    for ev in stream_final_answer(llm, question, pool, history=history or [],
                                  attached_names=attached_names or []):
        if ev["event"] == "token": toks.append(ev["data"])
        if ev["event"] == "citations": cites = json.loads(ev["data"])
    return "".join(toks), cites


def test_generate_over_pool_and_build_citations():
    pool = [_proj(), _attach()]
    llm = _StreamLLM("见资料[1][2]。")
    answer, cites = _run(llm, pool)
    assert answer == "见资料[1][2]。"
    assert [c["n"] for c in cites] == [1, 2]
    assert cites[1]["source_kind"] == "attachment"
    assert cites[1]["reference_id"] == 42
    assert cites[1]["char_start"] == 5 and cites[1]["char_end"] == 11
    # loop transcript discarded: GENERATE got system + (history) + the numbered 资料 only
    sent = " ".join(m["content"] for m in llm.messages)
    assert "【资料】" in sent and "项目片段TLB" in sent


def test_hallucinated_citation_dropped():
    pool = [_proj()]
    llm = _StreamLLM("见资料[1] 和 [9]。")   # [9] out of range
    _, cites = _run(llm, pool)
    assert [c["n"] for c in cites] == [1]


def test_empty_pool_direct_no_citations():
    llm = _StreamLLM("你好,有什么可以帮你?")
    answer, cites = _run(llm, [], question="你好")
    assert answer == "你好,有什么可以帮你?"
    assert cites == []
    sent = " ".join(m["content"] for m in llm.messages)
    assert "【资料】" not in sent           # direct path uses CHAT_SYS, no 资料 frame


def test_attached_names_injected_when_pool_present():
    llm = _StreamLLM("见[1]。")
    _run(llm, [_attach()], attached_names=["report.pdf"])
    sent = " ".join(m["content"] for m in llm.messages)
    assert "report.pdf" in sent and "附加" in sent
