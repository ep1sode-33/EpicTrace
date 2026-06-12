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


def test_fulltext_ref_injected_and_cited_on_agent_path(tmp_path: Path):
    db, cid = _setup(tmp_path)
    with db.session() as s:
        ref = ConversationReference(conversation_id=cid, kind="external", display_name="report.pdf",
                                    source_path="/x/report.pdf", extracted_text="页表全文内容",
                                    text_chars=6, mode="fulltext")
        s.add(ref); s.flush(); rid = ref.id
    # agent makes NO tool calls (fulltext already in pool); final GENERATE cites [1].
    chat_model = FakeChatModel(script=[AIMessage(content="不需要搜索")])
    gen_llm = FakeLLM(answer="见附件[1]。")
    svc = ChatService(db, gen_llm, _EmptyRetriever(), references=_Refs(db),
                      chat_model_factory=lambda: chat_model, supports_tools=lambda: True)
    events = list(svc.stream_answer(cid, "总结这个文件"))
    cites = json.loads(next(e for e in events if e["event"] == "citations")["data"])
    assert cites and cites[0]["reference_id"] == rid
    assert cites[0]["source_kind"] == "attachment"
    sent = " ".join(m["content"] for m in gen_llm.stream_messages[-1])
    assert "report.pdf" in sent and "页表全文内容" in sent


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
