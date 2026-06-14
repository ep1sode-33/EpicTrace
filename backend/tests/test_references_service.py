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
    svc.detach(cid, ref["id"])
    assert svc.list_active(cid) == []


def test_cumulative_budget_defers_second_file(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    svc = ReferenceService(db)
    # 窗口 = 1000 → 全文预算 500 token ≈ 1000 字符。第一个文件吃掉 ~450 token。
    a = _write(tmp_path, "a.md", "字" * 900)
    b = _write(tmp_path, "b.md", "字" * 400)
    ra = svc.add_external(cid, a, context_window=1000)
    rb = svc.add_external(cid, b, context_window=1000)
    assert ra["mode"] == "fulltext"
    assert rb["mode"] == "deferred"      # a 已占预算,b 累加后超 → deferred


def test_add_external_missing_file_raises_valueerror(tmp_path: Path):
    import pytest
    db, cid, _ = _setup(tmp_path)
    with pytest.raises(ValueError):
        ReferenceService(db).add_external(cid, str(tmp_path / "nope.md"), context_window=1_000_000)


def test_add_internal_rejects_cross_project(tmp_path: Path):
    import pytest
    db, cid, pid = _setup(tmp_path)
    # 另建一个项目并在其下建 ingest record
    from epictrace.models import IngestRecord, Project
    other = _write(tmp_path, "other.md", "别的项目的文件")
    with db.session() as s:
        p2 = Project(title="P2", folder_path=str(tmp_path / "p2")); s.add(p2); s.flush()
        rec = IngestRecord(project_id=p2.id, original_filename="other.md", stored_path=other,
                           content_hash="h", size_bytes=1, mtime=0.0, ingest_method="folder_scan",
                           extracted_text="x", indexed=True)
        s.add(rec); s.flush(); rid = rec.id
    with pytest.raises(ValueError):
        ReferenceService(db).add_internal(cid, rid, context_window=1_000_000)


def test_detach_wrong_conversation_is_noop(tmp_path: Path):
    db, cid, _ = _setup(tmp_path)
    svc = ReferenceService(db)
    ref = svc.add_external(cid, _write(tmp_path, "n.md", "内容内容"), context_window=1_000_000)
    svc.detach(999999, ref["id"])               # 不是该引用的会话 → 不动
    assert len(svc.list_active(cid)) == 1


def test_add_external_succeeds_even_if_provenance_write_fails(tmp_path: Path, monkeypatch):
    """provenance(content_list sidecar)派生/可选:DB 行已 commit 后写失败不得向上传播。"""
    db, cid, _ = _setup(tmp_path)
    path = _write(tmp_path, "paper.pdf", "x")  # 内容由假处理器给,文件存在即可

    from epictrace.interfaces.media import MediaResult

    class _PdfProc:
        def supports(self, _path):
            return True

        def process(self, _path):
            return MediaResult(text="页表把虚拟地址映射到物理地址", metadata={
                "backend": "mineru-hybrid",
                "content_list": [{"type": "text", "text": "hi", "page_idx": 0}],
                "pages": 1})

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("epictrace.services.references.get_processor", lambda p, config: _PdfProc())
    monkeypatch.setattr("epictrace.services.references.write_provenance", _boom)

    svc = ReferenceService(db)
    ref = svc.add_external(cid, path, context_window=1_000_000)  # 不应抛
    assert ref["extracted_text"].startswith("页表")
    active = svc.list_active(cid)
    assert len(active) == 1 and active[0]["id"] == ref["id"]
    sidecar = Path(tmp_path) / "provenance" / f"reference-{ref['id']}.json"
    assert not sidecar.exists()
