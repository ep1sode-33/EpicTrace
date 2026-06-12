import json
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Project
from epictrace.retrieval.types import RetrievedChunk
from epictrace.services.chat import ChatService
from epictrace.services.references import ReferenceService
from tests.fakes import FakeLLM


class _NoChunkRetriever:
    def retrieve(self, *, project_id, query, **kwargs):
        self.last_kwargs = kwargs
        return []


class _FocusSpyRetriever:
    def __init__(self): self.last_kwargs = None
    def retrieve(self, *, project_id, query, **kwargs):
        self.last_kwargs = kwargs
        return [RetrievedChunk(text="项目片段", ingest_record_id=99, project_id=project_id,
                               char_start=0, char_end=4, source_type="folder_scan")]


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid = c.id
    return db, cid


def test_fulltext_external_ref_injected_even_on_direct_route(tmp_path: Path):
    db, cid = _setup(tmp_path)
    f = tmp_path / "note.md"; f.write_text("页表把虚拟地址映射到物理地址", encoding="utf-8")
    refs = ReferenceService(db); ref = refs.add_external(cid, str(f), context_window=1_000_000)
    llm = FakeLLM(route="direct", answer="见资料[1]。")
    svc = ChatService(db, llm, _NoChunkRetriever(), references=refs)
    events = list(svc.stream_answer(cid, "讲讲这个文件"))
    cites = json.loads(next(e for e in events if e["event"] == "citations")["data"])
    assert cites and cites[0]["source_kind"] == "attachment" and cites[0]["reference_id"] == ref["id"]
    sent = llm.stream_messages[-1]
    assert "页表把虚拟地址" in sent[-1]["content"]


def test_focus_internal_ref_passes_ingest_ids_to_retriever(tmp_path: Path):
    db, cid = _setup(tmp_path)
    from epictrace.models import IngestRecord
    with db.session() as s:
        rec = IngestRecord(project_id=1, original_filename="f.md", stored_path=str(tmp_path / "f.md"),
                           content_hash="h", size_bytes=1, mtime=0.0, ingest_method="folder_scan",
                           extracted_text="x", indexed=True)
        (tmp_path / "f.md").write_text("一些较长的内容" * 50, encoding="utf-8")
        s.add(rec); s.flush(); rid = rec.id
    refs = ReferenceService(db); refs.add_internal(cid, rid, context_window=10)   # → focus
    spy = _FocusSpyRetriever()
    svc = ChatService(db, FakeLLM(route="retrieve", grade="sufficient", answer="答[1]。"), spy, references=refs)
    list(svc.stream_answer(cid, "聚焦提问"))
    assert spy.last_kwargs.get("ingest_record_ids") == [rid]


def test_no_references_behaves_like_plan3(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(route="direct", answer="你好"), _NoChunkRetriever())
    events = list(svc.stream_answer(cid, "你好"))
    assert json.loads(next(e for e in events if e["event"] == "citations")["data"]) == []
