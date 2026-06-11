import json
from pathlib import Path

from sqlalchemy import select

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Message, Project
from epictrace.retrieval.types import RetrievedChunk
from epictrace.services.chat import ChatService
from tests.fakes import FakeLLM, RaisingLLM


class _Retriever:
    def retrieve(self, *, project_id, query, k=6):
        return [RetrievedChunk(text="页表映射地址", ingest_record_id=1, project_id=project_id,
                               char_start=0, char_end=6, source_type="folder_scan")]


class _RaisingRetriever:
    def retrieve(self, *, project_id, query, k=6):
        raise RuntimeError("retriever boom")


def _setup(tmp_path, title="t"):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path / "P")); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title=title); s.add(c); s.flush()
        cid = c.id
    return db, cid


def _roles(db, cid):
    with db.session() as s:
        return [m.role for m in s.execute(
            select(Message).where(Message.conversation_id == cid).order_by(Message.id)
        ).scalars()]


def test_stream_emits_events_and_persists(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(grade="sufficient", answer="地址映射靠页表[1]。"), _Retriever())
    events = list(svc.stream_answer(cid, "页表是什么"))
    kinds = [e["event"] for e in events]
    assert "status" in kinds and "token" in kinds and "citations" in kinds and kinds[-1] == "done"
    answer = "".join(e["data"] for e in events if e["event"] == "token")
    assert "页表" in answer
    cite_evt = next(e for e in events if e["event"] == "citations")
    assert json.loads(cite_evt["data"])[0]["ingest_record_id"] == 1
    # 落库:user + assistant 两条
    with db.session() as s:
        msgs = list(s.execute(select(Message).where(Message.conversation_id == cid)).scalars())
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[1].citations_json and "ingest_record_id" in msgs[1].citations_json


def test_second_turn_includes_prior_turn_in_llm_messages(tmp_path: Path):
    db, cid = _setup(tmp_path)
    llm = FakeLLM(grade="sufficient", answer="第二轮答案[1]。")
    svc = ChatService(db, llm, _Retriever())
    list(svc.stream_answer(cid, "第一轮问题"))     # 第 1 轮:落 user+assistant
    llm.stream_messages.clear()                     # 只看第 2 轮的 stream 输入
    list(svc.stream_answer(cid, "第二轮问题"))     # 第 2 轮
    sent = llm.stream_messages[-1]
    contents = [m["content"] for m in sent]
    # 第 2 轮的 stream 消息里应含第 1 轮的内容(user 问 + assistant 答),且本轮问题在最后。
    assert any("第一轮问题" in c for c in contents)
    assert any("第二轮答案" in c for c in contents)
    assert "第二轮问题" in sent[-1]["content"]
    assert sent[0]["role"] == "system"               # 系统提示仍在最前


def test_first_turn_default_title_set_from_question_and_updated_at(tmp_path: Path):
    db, cid = _setup(tmp_path, title="新对话")
    svc = ChatService(db, FakeLLM(grade="sufficient", answer="答[1]。"), _Retriever())
    with db.session() as s:
        before = s.get(Conversation, cid).updated_at
    list(svc.stream_answer(cid, "操作系统的页表是如何工作的请详细说明一下谢谢" * 2))
    with db.session() as s:
        c = s.get(Conversation, cid)
        assert c.title != "新对话" and len(c.title) <= 30
        assert c.title.startswith("操作系统的页表")
        assert c.updated_at >= before


def test_nondefault_title_preserved(tmp_path: Path):
    db, cid = _setup(tmp_path, title="我的自定义标题")
    svc = ChatService(db, FakeLLM(grade="sufficient", answer="答[1]。"), _Retriever())
    list(svc.stream_answer(cid, "随便问点什么"))
    with db.session() as s:
        assert s.get(Conversation, cid).title == "我的自定义标题"


def test_llm_error_yields_error_event_and_no_assistant_message(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, RaisingLLM(), _Retriever())
    events = list(svc.stream_answer(cid, "问题"))
    kinds = [e["event"] for e in events]
    assert "error" in kinds and "done" not in kinds
    # user 消息可保留,但绝不落半截 assistant 消息。
    assert _roles(db, cid) == ["user"]


def test_retriever_error_yields_error_event_and_no_assistant_message(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(grade="sufficient"), _RaisingRetriever())
    events = list(svc.stream_answer(cid, "问题"))
    assert [e["event"] for e in events][-1] == "error"
    assert _roles(db, cid) == ["user"]
