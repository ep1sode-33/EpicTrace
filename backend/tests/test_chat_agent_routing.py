import json
from pathlib import Path

from langchain_core.messages import AIMessage

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, ConversationReference, Project
from epictrace.retrieval.types import RetrievedChunk
from epictrace.services.chat import ChatService
from tests.fakes import FakeChatModel, FakeLLM


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid = c.id
    return db, cid


class _Refs:
    def __init__(self, db): self._db = db
    def list_active(self, cid):
        from epictrace.services.references import ReferenceService
        return ReferenceService(self._db).list_active(cid)


class _ProjRetriever:
    def retrieve(self, *, project_id, query, **kwargs):
        return [RetrievedChunk(text="项目TLB片段", ingest_record_id=7, project_id=project_id,
                               char_start=0, char_end=6, source_type="folder_scan")]


class _EmptyRetriever:
    def retrieve(self, *, project_id, query, **kwargs): return []


def _call(name, args, cid="1"):
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


def test_supported_profile_uses_agent_path_and_cites(tmp_path: Path):
    db, cid = _setup(tmp_path)
    # agent loop: search once → stop; final GENERATE streams an answer with [1].
    chat_model = FakeChatModel(script=[
        AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "TLB"})]),
        AIMessage(content="done"),
    ])
    gen_llm = FakeLLM(answer="据资料[1]。")
    svc = ChatService(db, gen_llm, _ProjRetriever(), references=_Refs(db),
                      chat_model_factory=lambda: chat_model, supports_tools=lambda: True)
    events = list(svc.stream_answer(cid, "TLB是什么"))
    cites = json.loads(next(e for e in events if e["event"] == "citations")["data"])
    assert cites and cites[0]["ingest_record_id"] == 7
    tokens = "".join(e["data"] for e in events if e["event"] == "token")
    assert tokens == "据资料[1]。"


def test_unsupported_profile_matches_plan5_behavior(tmp_path: Path):
    db, cid = _setup(tmp_path)
    # supports_tools False → existing Plan 5 pipeline (route/grade via FakeLLM, project RAG).
    llm = FakeLLM(route="retrieve", grade="sufficient", answer="项目答[1]。")
    svc = ChatService(db, llm, _ProjRetriever(), references=_Refs(db),
                      chat_model_factory=lambda: FakeChatModel(script=[]),
                      supports_tools=lambda: False)
    list(svc.stream_answer(cid, "TLB怎么算"))
    sent = " ".join(m["content"] for m in llm.stream_messages[-1])
    assert "项目TLB片段" in sent     # Plan 5 project RAG injected, unchanged behavior


def test_no_factory_falls_back_to_plan5(tmp_path: Path):
    db, cid = _setup(tmp_path)
    llm = FakeLLM(route="retrieve", grade="sufficient", answer="答[1]。")
    # no chat_model_factory at all → must behave exactly like current Plan 5
    svc = ChatService(db, llm, _ProjRetriever(), references=_Refs(db))
    list(svc.stream_answer(cid, "TLB怎么算"))
    sent = " ".join(m["content"] for m in llm.stream_messages[-1])
    assert "项目TLB片段" in sent


def test_fulltext_ref_greeting_no_toolcall_is_ungrounded(tmp_path: Path):
    """NEW CONTRACT (was test_fulltext_ref_injected_and_cited_on_agent_path): a small
    fulltext attachment is no longer auto-seeded into the pool — it's reachable only via the
    read_attachment tool. So if the model calls NO tools (e.g. a greeting), the pool stays
    EMPTY → the necessity gate fires → a DIRECT chat answer with NO citation and NO 【资料】
    framing (ChatGPT-like: the model decides whether to open the file). The fulltext id is
    still listed in the readable-attachment manifest so the model COULD open it."""
    db, cid = _setup(tmp_path)
    with db.session() as s:
        ref = ConversationReference(conversation_id=cid, kind="external", display_name="report.pdf",
                                    source_path="/x/report.pdf", extracted_text="页表全文内容",
                                    text_chars=6, mode="fulltext")
        s.add(ref); s.flush(); rid = ref.id
    # agent makes NO tool calls (greeting) → pool empty → direct/ungrounded answer.
    chat_model = FakeChatModel(script=[AIMessage(content="不需要搜索")])
    gen_llm = FakeLLM(answer="你好,有什么可以帮你?")
    svc = ChatService(db, gen_llm, _EmptyRetriever(), references=_Refs(db),
                      chat_model_factory=lambda: chat_model, supports_tools=lambda: True)
    events = list(svc.stream_answer(cid, "你好"))
    cites = json.loads(next(e for e in events if e["event"] == "citations")["data"])
    assert cites == []                                  # ungrounded → no citation
    # direct chat: CHAT_SYS, no 【资料】 framing, the fulltext text is NOT injected.
    sent_msgs = gen_llm.stream_messages[-1]
    assert sent_msgs[0]["content"] == "你是有帮助的助手,用中文简洁作答。"
    sent = " ".join(m["content"] for m in sent_msgs)
    assert "【资料】" not in sent and "页表全文内容" not in sent
    # but the fulltext id IS advertised to the model as a readable attachment.
    loop_sys = chat_model.invocations[0][0].content
    assert f"id={rid}" in loop_sys and "report.pdf" in loop_sys


def test_fulltext_ref_read_via_tool_lands_in_pool_and_cited(tmp_path: Path):
    """When the model DOES choose to open the fulltext file via read_attachment, the WHOLE
    file content lands in the pool and the final GENERATE answer cites it [1] (whole-doc
    attachment reference)."""
    db, cid = _setup(tmp_path)
    with db.session() as s:
        ref = ConversationReference(conversation_id=cid, kind="external", display_name="report.pdf",
                                    source_path="/x/report.pdf", extracted_text="页表全文内容",
                                    text_chars=6, mode="fulltext")
        s.add(ref); s.flush(); rid = ref.id
    chat_model = FakeChatModel(script=[
        AIMessage(content="", tool_calls=[_call("read_attachment", {"reference_id": rid})]),
        AIMessage(content="读完了"),
    ])
    gen_llm = FakeLLM(answer="见附件[1]。")
    svc = ChatService(db, gen_llm, _EmptyRetriever(), references=_Refs(db),
                      chat_model_factory=lambda: chat_model, supports_tools=lambda: True)
    events = list(svc.stream_answer(cid, "总结这个文件"))
    cites = json.loads(next(e for e in events if e["event"] == "citations")["data"])
    assert cites and cites[0]["reference_id"] == rid
    assert cites[0]["source_kind"] == "attachment"
    sent = " ".join(m["content"] for m in gen_llm.stream_messages[-1])
    assert "report.pdf" in sent and "页表全文内容" in sent


def test_indexed_attachment_manifest_reaches_model(tmp_path: Path):
    db, cid = _setup(tmp_path)
    with db.session() as s:
        ref = ConversationReference(conversation_id=cid, kind="external", display_name="big.pdf",
                                    source_path="/x/big.pdf", extracted_text="一二三四五六七八",
                                    text_chars=8, mode="indexed")
        s.add(ref); s.flush(); rid = ref.id
    # agent makes no tool calls; we only assert the readable-attachment manifest (with the
    # reference_id) is present in the loop system prompt the model sees.
    chat_model = FakeChatModel(script=[AIMessage(content="不需要搜索")])
    gen_llm = FakeLLM(answer="好的。")
    svc = ChatService(db, gen_llm, _EmptyRetriever(), references=_Refs(db),
                      chat_model_factory=lambda: chat_model, supports_tools=lambda: True)
    list(svc.stream_answer(cid, "总结 big.pdf"))
    sys_text = chat_model.invocations[0][0].content   # SystemMessage of the loop prompt
    assert f"id={rid}" in sys_text and "big.pdf" in sys_text


def test_agent_exception_falls_back_to_plan5(tmp_path: Path):
    db, cid = _setup(tmp_path)

    class _BoomFactory:
        def __call__(self): raise RuntimeError("chat model construction boom")

    llm = FakeLLM(route="retrieve", grade="sufficient", answer="回退答[1]。")
    svc = ChatService(db, llm, _ProjRetriever(), references=_Refs(db),
                      chat_model_factory=_BoomFactory(), supports_tools=lambda: True)
    events = list(svc.stream_answer(cid, "TLB"))
    # falls back to Plan 5: project RAG still injected, answer produced (no error event).
    assert not any(e["event"] == "error" for e in events)
    sent = " ".join(m["content"] for m in llm.stream_messages[-1])
    assert "项目TLB片段" in sent


def test_supports_tools_probe_exception_degrades_to_plan5(tmp_path: Path):
    """FIX 2: a probe (supports_tools) that raises must NOT abort the turn — treat as
    unsupported and degrade cleanly to the Plan 5 pipeline."""
    db, cid = _setup(tmp_path)

    def boom_gate():
        raise RuntimeError("probe boom")

    llm = FakeLLM(route="retrieve", grade="sufficient", answer="回退答[1]。")
    svc = ChatService(db, llm, _ProjRetriever(), references=_Refs(db),
                      chat_model_factory=lambda: FakeChatModel(script=[]), supports_tools=boom_gate)
    events = list(svc.stream_answer(cid, "TLB"))
    assert not any(e["event"] == "error" for e in events)
    sent = " ".join(m["content"] for m in llm.stream_messages[-1])
    assert "项目TLB片段" in sent     # Plan 5 ran, project RAG injected


class _StreamOneThenBoomLLM:
    """Streams exactly one token, then raises mid-stream (answer-phase failure AFTER a
    token has been yielded). complete() is a no-op stub for titles."""

    def __init__(self):
        self.stream_messages: list[list[dict]] = []

    def complete(self, messages, **kwargs):
        return "标题"

    def stream(self, messages, **kwargs):
        self.stream_messages.append(list(messages))
        yield "据"           # one answer token escapes to the client
        raise RuntimeError("answer-phase boom after a token")


def test_partial_agent_answer_does_not_double_stream(tmp_path: Path):
    """FIX 1 regression: once an agent answer TOKEN has been yielded, an exception in the
    answer phase must be caught internally — NEVER falling back to a second Plan 5 answer.
    The project-RAG fallback content must be absent and no duplicate answer streamed."""
    db, cid = _setup(tmp_path)
    chat_model = FakeChatModel(script=[
        AIMessage(content="", tool_calls=[_call("search_project_library", {"query": "TLB"})]),
        AIMessage(content="done"),
    ])
    gen_llm = _StreamOneThenBoomLLM()
    # _ProjRetriever returns "项目TLB片段"; if Plan 5 fallback re-ran, that text would be
    # streamed/injected a second time. It must NOT be.
    svc = ChatService(db, gen_llm, _ProjRetriever(), references=_Refs(db),
                      chat_model_factory=lambda: chat_model, supports_tools=lambda: True)
    events = list(svc.stream_answer(cid, "TLB是什么"))
    tokens = "".join(e["data"] for e in events if e["event"] == "token")
    assert tokens == "据"                       # only the single agent token, no second answer
    # exactly one terminal event, and no error event leaked to the client
    assert sum(1 for e in events if e["event"] == "done") == 1
    assert not any(e["event"] == "error" for e in events)
    # The answer stream ran EXACTLY once — Plan 5 did not re-run a second answer stream.
    assert len(gen_llm.stream_messages) == 1
