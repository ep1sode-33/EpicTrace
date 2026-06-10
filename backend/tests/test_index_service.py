from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.services.projects import ProjectService
from epictrace.services.scan import ScanService
from epictrace.services.index import IndexService
from epictrace.vectorstore.milvus_lite import MilvusLiteStore
from tests.fakes import FakeEmbedder


def _setup(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    folder = Path(proj.folder_path)
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    svc = IndexService(db, embedder=FakeEmbedder(), vector_store=store)
    return db, proj, folder, store, svc


def test_index_extracts_chunks_embeds_and_flips_indexed(tmp_path):
    db, proj, folder, store, svc = _setup(tmp_path)
    (folder / "note.md").write_text("虚拟内存\n\n" + "page table " * 300, encoding="utf-8")
    ScanService(db).scan_and_register(proj.id)

    job = svc.index_project(proj.id)
    assert job.total == 1 and job.done == 1 and job.status == "done"

    # 文件翻成已索引
    from epictrace.services.ingest import IngestService
    recs = IngestService(db).list_for_project(proj.id)
    assert all(r.indexed for r in recs)

    # 向量进了库
    hits = store.query(FakeEmbedder().embed(["page table"])[0], filter={"project_id": proj.id}, k=3)
    assert len(hits) >= 1


def test_index_skips_image_and_audio(tmp_path):
    db, proj, folder, store, svc = _setup(tmp_path)
    (folder / "pic.png").write_bytes(b"\x89PNG\r\n")
    (folder / "snd.mp3").write_bytes(b"ID3")
    ScanService(db).scan_and_register(proj.id)  # 注:.png/.mp3 不在 INDEXABLE_SUFFIXES,扫描就不会登记
    job = svc.index_project(proj.id)
    assert job.total == 0  # 没有可索引文件


def test_index_single_file_failure_is_recorded_not_fatal(tmp_path, monkeypatch):
    db, proj, folder, store, svc = _setup(tmp_path)
    (folder / "a.md").write_text("a", encoding="utf-8")
    (folder / "b.md").write_text("b", encoding="utf-8")
    ScanService(db).scan_and_register(proj.id)
    # 让某个文件提取时抛错
    import epictrace.services.index as idx
    real = idx.get_processor
    def boom(p):
        if p.name == "a.md":
            class P:
                def process(self, _): raise RuntimeError("boom")
                def supports(self, _): return True
            return P()
        return real(p)
    monkeypatch.setattr(idx, "get_processor", boom)
    job = svc.index_project(proj.id)
    assert job.done == 1 and len(job.errors) == 1     # b 成功, a 记错
    assert job.status == "done"


def test_status_for_unknown_project_total_zero(tmp_path):
    db, proj, folder, store, svc = _setup(tmp_path)
    job = svc.index_project(99999)
    assert job.total == 0
