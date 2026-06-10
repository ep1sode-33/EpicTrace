from pathlib import Path
from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.services.projects import ProjectService
from epictrace.services.scan import ScanService


def _setup(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    return db, proj, Path(proj.folder_path)


def test_scan_registers_indexable_files_in_place(tmp_path):
    db, proj, folder = _setup(tmp_path)
    (folder / "note.md").write_text("hello virtual memory", encoding="utf-8")
    (folder / "data.bin").write_bytes(b"\x00\x01")          # 非可索引后缀 → 跳过
    # 旧版二进制 Office 格式无 processor 可读,已移出白名单 → 跳过(否则永远卡住)
    (folder / "legacy.doc").write_bytes(b"\xd0\xcf\x11\xe0")
    (folder / "legacy.ppt").write_bytes(b"\xd0\xcf\x11\xe0")
    (folder / "node_modules").mkdir()
    (folder / "node_modules" / "junk.js").write_text("x", encoding="utf-8")  # 忽略目录 → 跳过

    svc = ScanService(db)
    result = svc.scan_and_register(proj.id)

    assert result.added == 1
    recs = svc.list_pending(proj.id)
    assert len(recs) == 1
    r = recs[0]
    assert r.original_filename == "note.md"
    assert r.stored_path == str(folder / "note.md")   # 就地:指向原路径,未复制
    assert r.ingest_method == "folder_scan"
    assert r.indexed is False
    assert r.extracted_text == ""   # 扫描只登记,不提取(提取统一在索引时做)


def test_rescan_only_adds_new_files(tmp_path):
    db, proj, folder = _setup(tmp_path)
    (folder / "a.md").write_text("a", encoding="utf-8")
    svc = ScanService(db)
    assert svc.scan_and_register(proj.id).added == 1
    # 再扫:无新文件
    assert svc.scan_and_register(proj.id).added == 0
    # 加一个新文件再扫
    (folder / "b.txt").write_text("b", encoding="utf-8")
    r2 = svc.scan_and_register(proj.id)
    assert r2.added == 1
    assert len(svc.list_pending(proj.id)) == 2


def test_rescan_flags_missing(tmp_path):
    db, proj, folder = _setup(tmp_path)
    f = folder / "a.md"; f.write_text("a", encoding="utf-8")
    svc = ScanService(db)
    svc.scan_and_register(proj.id)
    f.unlink()
    result = svc.scan_and_register(proj.id)
    assert result.missing == 1


def test_scan_skips_unreadable_file_and_continues(tmp_path, monkeypatch):
    db, proj, folder = _setup(tmp_path)
    bad = folder / "bad.md"; bad.write_text("boom", encoding="utf-8")
    good = folder / "good.md"; good.write_text("fine", encoding="utf-8")

    import epictrace.services.scan as scan_mod

    real_sha256 = scan_mod._sha256

    def fake_sha256(p):  # noqa: ANN001
        if p.name == "bad.md":
            raise OSError("simulated unreadable file")
        return real_sha256(p)

    monkeypatch.setattr(scan_mod, "_sha256", fake_sha256)

    svc = ScanService(db)
    result = svc.scan_and_register(proj.id)  # 不应抛异常

    assert result.added == 1
    recs = svc.list_pending(proj.id)
    assert len(recs) == 1
    assert recs[0].original_filename == "good.md"
