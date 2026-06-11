from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, IngestRecord, Project
from epictrace.services.references import ReferenceService

BIG_WIN = 1_000_000     # 预算极大 → 一定 fulltext
TINY_WIN = 10           # 预算极小 → 外部 deferred / 内部 focus


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid, pid = c.id, p.id
    return db, cid, pid


def _write(tmp_path, name, text):
    f = tmp_path / name; f.write_text(text, encoding="utf-8"); return str(f)


def test_add_external_small_is_fulltext_and_caches_text(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "note.md", "页表把虚拟地址映射到物理地址")
    svc = ReferenceService(db)
    ref = svc.add_external(cid, path, context_window=BIG_WIN)
    assert ref["kind"] == "external" and ref["mode"] == "fulltext"
    assert ref["display_name"] == "note.md"
    active = svc.list_active(cid)
    assert len(active) == 1 and active[0]["extracted_text"].startswith("页表")


def test_add_external_too_big_is_deferred(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "big.md", "字" * 500)
    ref = ReferenceService(db).add_external(cid, path, context_window=TINY_WIN)
    assert ref["mode"] == "deferred"


def test_add_external_rejects_empty_and_unsupported(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    import pytest
    empty = _write(tmp_path, "empty.md", "   ")
    with pytest.raises(ValueError):
        ReferenceService(db).add_external(cid, empty, context_window=BIG_WIN)
    weird = _write(tmp_path, "x.unknownext", "data")
    with pytest.raises(ValueError):
        ReferenceService(db).add_external(cid, weird, context_window=BIG_WIN)


def test_add_internal_small_fulltext_large_focus(tmp_path: Path):
    db, cid, pid = _setup(tmp_path)
    body = "短内容" * 8   # 24 字 → 12 token,超过 TINY_WIN 的 5 token 预算,但远低于 BIG_WIN
    small = _write(tmp_path, "small.md", body)
    with db.session() as s:
        rec = IngestRecord(project_id=pid, original_filename="small.md", stored_path=small,
                           content_hash="h", size_bytes=len(body.encode()), mtime=0.0,
                           ingest_method="folder_scan", extracted_text=body, indexed=True)
        s.add(rec); s.flush(); rid = rec.id
    svc = ReferenceService(db)
    ref = svc.add_internal(cid, rid, context_window=BIG_WIN)
    assert ref["kind"] == "internal" and ref["mode"] == "fulltext" and ref["ingest_record_id"] == rid
    ref2 = svc.add_internal(cid, rid, context_window=TINY_WIN)
    assert ref2["mode"] == "focus"


def test_detach_drops_from_active(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "n.md", "内容内容")
    svc = ReferenceService(db)
    ref = svc.add_external(cid, path, context_window=BIG_WIN)
    svc.detach(ref["id"])
    assert svc.list_active(cid) == []
