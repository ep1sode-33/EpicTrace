from pathlib import Path

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, IngestRecord, Project
from epictrace.services.references import ReferenceService


def _setup(tmp_path, n_projects=1):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    ids = []
    with db.session() as s:
        for i in range(n_projects):
            p = Project(title=f"P{i}", folder_path=str(tmp_path)); s.add(p); s.flush()
            c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
            ids.append((p.id, c.id))
    return db, ids


def _w(tmp_path, name, text):
    f = tmp_path / name; f.write_text(text, encoding="utf-8"); return str(f)


def test_cumulative_budget_defers_second_file(tmp_path: Path):
    db, [(_pid, cid)] = _setup(tmp_path)
    svc = ReferenceService(db)
    win = 1000  # 预算 = 500 token ≈ 1000 字符;两个 ~300 token 文件:第一个进,第二个累加超 → deferred
    r1 = svc.add_external(cid, _w(tmp_path, "a.md", "字" * 600), context_window=win)
    r2 = svc.add_external(cid, _w(tmp_path, "b.md", "字" * 600), context_window=win)
    assert r1["mode"] == "fulltext"
    assert r2["mode"] == "deferred"


def test_add_external_missing_file_raises(tmp_path: Path):
    db, [(_pid, cid)] = _setup(tmp_path)
    with pytest.raises(ValueError):
        ReferenceService(db).add_external(cid, str(tmp_path / "nope.md"), context_window=1_000_000)


def test_add_internal_rejects_cross_project(tmp_path: Path):
    db, ids = _setup(tmp_path, n_projects=2)
    (_pidA, cidA), (pidB, _cidB) = ids
    with db.session() as s:
        rec = IngestRecord(project_id=pidB, original_filename="b.md",
                           stored_path=_w(tmp_path, "b.md", "x"), content_hash="h",
                           size_bytes=1, mtime=0.0, ingest_method="folder_scan",
                           extracted_text="x", indexed=True)
        s.add(rec); s.flush(); rid = rec.id
    with pytest.raises(ValueError):
        ReferenceService(db).add_internal(cidA, rid, context_window=1_000_000)


def test_detach_scoped_to_conversation(tmp_path: Path):
    db, [(_pid, cid)] = _setup(tmp_path)
    svc = ReferenceService(db)
    ref = svc.add_external(cid, _w(tmp_path, "n.md", "内容内容"), context_window=1_000_000)
    svc.detach(cid + 999, ref["id"])      # 错误的 conversation id → 不解挂
    assert len(svc.list_active(cid)) == 1
    svc.detach(cid, ref["id"])            # 正确 → 解挂
    assert svc.list_active(cid) == []
