from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Project
from epictrace.services.references import ReferenceService
from tests.fakes import FakeEmbedder, FakeVectorStore

TINY = 10


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid = c.id
    return db, cid


def _w(tmp_path, name, text):
    f = tmp_path / name; f.write_text(text, encoding="utf-8"); return str(f)


def test_large_external_is_indexed_into_attachment_store(tmp_path: Path):
    db, cid = _setup(tmp_path)
    store = FakeVectorStore()
    svc = ReferenceService(db, embedder=FakeEmbedder(), attachment_store=store)
    ref = svc.add_external(cid, _w(tmp_path, "big.md", "页表把虚拟地址映射到物理地址。" * 50), TINY)
    assert ref["mode"] == "indexed"
    recs = store.list_by({"conversation_id": cid, "reference_id": ref["id"]})
    assert len(recs) >= 1
    r0 = recs[0]
    assert r0["conversation_id"] == cid and r0["reference_id"] == ref["id"]
    assert "char_start" in r0 and "char_end" in r0 and r0["source_type"] == "attachment"


def test_indexing_failure_falls_back_to_deferred(tmp_path: Path):
    db, cid = _setup(tmp_path)
    class _BoomEmbedder(FakeEmbedder):
        def embed(self, texts): raise RuntimeError("embed boom")
    svc = ReferenceService(db, embedder=_BoomEmbedder(), attachment_store=FakeVectorStore())
    ref = svc.add_external(cid, _w(tmp_path, "big.md", "内容" * 100), TINY)
    assert ref["mode"] == "deferred"


def test_detach_cleans_attachment_vectors(tmp_path: Path):
    db, cid = _setup(tmp_path)
    store = FakeVectorStore()
    svc = ReferenceService(db, embedder=FakeEmbedder(), attachment_store=store)
    ref = svc.add_external(cid, _w(tmp_path, "big.md", "内容内容" * 100), TINY)
    assert store.list_by({"reference_id": ref["id"]})
    svc.detach(cid, ref["id"])
    assert store.list_by({"reference_id": ref["id"]}) == []


def test_detach_wrong_conversation_does_not_delete_vectors(tmp_path: Path):
    db, cid = _setup(tmp_path)
    store = FakeVectorStore()
    svc = ReferenceService(db, embedder=FakeEmbedder(), attachment_store=store)
    ref = svc.add_external(cid, _w(tmp_path, "big.md", "内容内容" * 100), TINY)
    svc.detach(cid + 999, ref["id"])                      # 错误的会话 id
    assert store.list_by({"reference_id": ref["id"]})     # 向量未被误删
    svc.detach(cid, ref["id"])                            # 正确 → 删
    assert store.list_by({"reference_id": ref["id"]}) == []


def test_small_external_still_fulltext_no_indexing(tmp_path: Path):
    db, cid = _setup(tmp_path)
    store = FakeVectorStore()
    svc = ReferenceService(db, embedder=FakeEmbedder(), attachment_store=store)
    ref = svc.add_external(cid, _w(tmp_path, "s.md", "短"), context_window=1_000_000)
    assert ref["mode"] == "fulltext" and store.records == []
