import json
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, ConversationReference, Project
from epictrace.retrieval.types import RetrievedChunk
from epictrace.services.chat import ChatService
from tests.fakes import FakeLLM


class _EmptyRetriever:
    def retrieve(self, *, project_id, query, **kwargs):
        return []


class _FakeAttachmentRetriever:
    def __init__(self): self.calls = []
    def retrieve(self, *, conversation_id, reference_ids, query, k=6):
        self.calls.append((conversation_id, tuple(reference_ids)))
        return [RetrievedChunk(text="附件相关片段", ingest_record_id=0, project_id=0,
                               char_start=5, char_end=11, source_type="attachment",
                               source_kind="attachment", reference_id=reference_ids[0])]


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid = c.id
    return db, cid


def _add_indexed_ref(db, cid) -> int:
    with db.session() as s:
        ref = ConversationReference(conversation_id=cid, kind="external", display_name="big.md",
                                    source_path="/x/big.md", extracted_text="…", text_chars=1,
                                    mode="indexed")
        s.add(ref); s.flush(); return ref.id


class _Refs:
    def __init__(self, db): self._db = db
    def list_active(self, cid):
        from epictrace.services.references import ReferenceService
        return ReferenceService(self._db).list_active(cid)


def test_indexed_ref_pulls_attachment_chunks_and_cites_them(tmp_path: Path):
    db, cid = _setup(tmp_path)
    rid = _add_indexed_ref(db, cid)
    attach = _FakeAttachmentRetriever()
    svc = ChatService(db, FakeLLM(route="retrieve", grade="sufficient", answer="见附件[1]。"),
                      _EmptyRetriever(), references=_Refs(db), attachment_retriever=attach)
    events = list(svc.stream_answer(cid, "这个文件讲了什么"))
    assert attach.calls == [(cid, (rid,))]
    cites = json.loads(next(e for e in events if e["event"] == "citations")["data"])
    assert cites and cites[0]["source_kind"] == "attachment" and cites[0]["reference_id"] == rid
    assert cites[0]["char_start"] == 5 and cites[0]["char_end"] == 11


def test_no_attachment_retriever_or_no_indexed_ref_is_noop(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(route="direct", answer="你好"), _EmptyRetriever())
    events = list(svc.stream_answer(cid, "你好"))
    assert json.loads(next(e for e in events if e["event"] == "citations")["data"]) == []


def test_attachment_retriever_factory_not_called_without_indexed_refs(tmp_path: Path):
    db, cid = _setup(tmp_path)
    built = []
    def factory():
        built.append(1)
        return _FakeAttachmentRetriever()
    svc = ChatService(db, FakeLLM(route="direct", answer="你好"), _EmptyRetriever(),
                      references=_Refs(db), attachment_retriever=factory)
    list(svc.stream_answer(cid, "你好"))   # 无 indexed 引用 → 工厂不该被调用
    assert built == []


def test_attachment_retriever_factory_called_with_indexed_refs(tmp_path: Path):
    db, cid = _setup(tmp_path)
    rid = _add_indexed_ref(db, cid)
    inner = _FakeAttachmentRetriever()
    svc = ChatService(db, FakeLLM(route="retrieve", grade="sufficient", answer="见[1]。"),
                      _EmptyRetriever(), references=_Refs(db), attachment_retriever=lambda: inner)
    list(svc.stream_answer(cid, "讲讲文件"))
    assert inner.calls == [(cid, (rid,))]   # 工厂解析出的 retriever 被调用
