from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.services.projects import ProjectService
from epictrace.services.scan import ScanService
from epictrace.services.index import IndexService
from epictrace.vectorstore.milvus_lite import MilvusLiteStore
from tests.fakes import FakeEmbedder, FakeVectorStore


def _setup(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    folder = Path(proj.folder_path)
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    svc = IndexService(db, embedder=FakeEmbedder(), vector_store=store)
    return db, proj, folder, store, svc


def _setup_fake(tmp_path: Path):
    """同 _setup,但用 FakeVectorStore 以断言 delete_by_project 被调用。"""
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    folder = Path(proj.folder_path)
    store = FakeVectorStore()
    svc = IndexService(db, embedder=FakeEmbedder(), vector_store=store)
    return db, proj, folder, store, svc


def test_index_extracts_chunks_embeds_and_flips_indexed(tmp_path):
    db, proj, folder, store, svc = _setup(tmp_path)
    (folder / "note.md").write_text("虚拟内存\n\n" + "page table " * 300, encoding="utf-8")
    ScanService(db).scan_and_register(proj.id)

    job = svc.index_project(proj.id)
    assert job.total == 1 and job.done == 0 and job.status == "running"
    svc._run(job)  # 同步跑完后台工作,再断终态
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
    svc._run(job)
    assert job.total == 0  # 没有可索引文件


def test_index_single_file_failure_is_recorded_not_fatal(tmp_path, monkeypatch):
    db, proj, folder, store, svc = _setup(tmp_path)
    (folder / "a.md").write_text("a", encoding="utf-8")
    (folder / "b.md").write_text("b", encoding="utf-8")
    ScanService(db).scan_and_register(proj.id)
    # 让某个文件提取时抛错
    import epictrace.services.index as idx
    real = idx.get_processor
    def boom(p, config):
        if p.name == "a.md":
            class P:
                def process(self, _): raise RuntimeError("boom")
                def supports(self, _): return True
            return P()
        return real(p, config)
    monkeypatch.setattr(idx, "get_processor", boom)
    job = svc.index_project(proj.id)
    svc._run(job)
    assert job.done == 1 and len(job.errors) == 1     # b 成功, a 记错
    assert job.status == "done"


def test_status_for_unknown_project_total_zero(tmp_path):
    db, proj, folder, store, svc = _setup(tmp_path)
    job = svc.index_project(99999)
    svc._run(job)
    assert job.total == 0


def test_reindex_clears_vectors_resets_records_and_runs(tmp_path):
    db, proj, folder, store, svc = _setup_fake(tmp_path)
    (folder / "note.md").write_text("虚拟内存\n\n" + "page table " * 300, encoding="utf-8")
    ScanService(db).scan_and_register(proj.id)

    # 先正常索引一遍,使记录翻成 indexed=True 且库里有向量。
    svc._run(svc.index_project(proj.id))
    from epictrace.services.ingest import IngestService
    assert all(r.indexed for r in IngestService(db).list_for_project(proj.id))

    # 重建:返回 running 的 job,且 total 含全部可索引文件(无视已索引)。
    job = svc.reindex_project(proj.id)
    assert job.status == "running" and job.total == 1

    # (a) 清向量:delete_by_project 被以该项目调用。
    assert proj.id in store.deleted_projects
    # (b) 重置:重建前所有记录被翻回 indexed=False(在 _run 之前)。
    assert all(not r.indexed for r in IngestService(db).list_for_project(proj.id))

    # 跑后台工作:重新提取 + 重新入库 + 翻回 indexed=True。
    svc._run(job)
    assert job.done == 1 and job.status == "done"
    assert all(r.indexed for r in IngestService(db).list_for_project(proj.id))


def test_reindex_best_effort_deletes_provenance_sidecars(tmp_path):
    from epictrace.services.ingest import IngestService

    db, proj, folder, store, svc = _setup_fake(tmp_path)
    (folder / "note.md").write_text("page table " * 50, encoding="utf-8")
    ScanService(db).scan_and_register(proj.id)
    rec_id = IngestService(db).list_for_project(proj.id)[0].id

    # 放一个该项目记录的 provenance sidecar,断言重建会尽力删掉它。
    prov = db.config.provenance_dir
    prov.mkdir(parents=True, exist_ok=True)
    sidecar = prov / f"ingest-{rec_id}.json"
    sidecar.write_text("[]", encoding="utf-8")

    svc.reindex_project(proj.id)
    assert not sidecar.exists()


def test_reindex_unknown_project_is_noop_job(tmp_path):
    db, proj, folder, store, svc = _setup_fake(tmp_path)
    job = svc.reindex_project(99999)
    svc._run(job)
    assert job.total == 0
