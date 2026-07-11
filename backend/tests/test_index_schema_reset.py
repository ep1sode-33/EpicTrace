"""reset_index_if_schema_upgraded:向量 schema 版本升级的一次性重置(D4 第二层)。

标记文件 data_dir/vector_schema_version 不是当前版本(缺失或旧值)→ 全库
IngestRecord.indexed=False + 写入标记;已是当前版本 → 零副作用。幂等。
create_app 启动时接线调用,失败只记日志不挡启动。
"""
from pathlib import Path

from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import IngestRecord, Project
from epictrace.services.index import INDEX_SCHEMA_VERSION, reset_index_if_schema_upgraded


def _db_with_indexed_records(tmp_path: Path, n: int = 2):
    """建 tmp 隔离的库,预置 n 条 indexed=True 的记录(两个项目,覆盖'全库'语义)。"""
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    rec_ids = []
    with db.session() as s:
        for i in range(n):
            proj = Project(title=f"P{i}", folder_path=str(tmp_path / f"P{i}"))
            s.add(proj); s.flush()
            rec = IngestRecord(project_id=proj.id, original_filename=f"a{i}.md",
                               stored_path=str(tmp_path / f"a{i}.md"), content_hash="h",
                               size_bytes=1, mtime=0.0, ingest_method="folder_scan",
                               description="", indexed=True)
            s.add(rec); s.flush()
            rec_ids.append(rec.id)
    return db, rec_ids


def _indexed_flags(db: Database, rec_ids: list[int]) -> list[bool]:
    with db.session() as s:
        return [s.get(IngestRecord, rid).indexed for rid in rec_ids]


def test_missing_marker_resets_all_and_writes_marker(tmp_path, caplog):
    db, rec_ids = _db_with_indexed_records(tmp_path)
    with caplog.at_level("INFO", logger="epictrace"):
        assert reset_index_if_schema_upgraded(db) is True
    assert _indexed_flags(db, rec_ids) == [False, False]          # 全库翻回待索引
    marker = tmp_path / "vector_schema_version"
    assert marker.read_text(encoding="utf-8") == INDEX_SCHEMA_VERSION == "2"
    assert any("待索引" in r.message for r in caplog.records)      # info 日志可见


def test_second_call_is_noop(tmp_path):
    db, rec_ids = _db_with_indexed_records(tmp_path)
    assert reset_index_if_schema_upgraded(db) is True
    # 之后重新索引了一条 —— 二次调用(标记已一致)不得再碰它。
    with db.session() as s:
        s.get(IngestRecord, rec_ids[0]).indexed = True
    assert reset_index_if_schema_upgraded(db) is False            # 幂等:no-op
    assert _indexed_flags(db, rec_ids) == [True, False]


def test_current_marker_means_zero_side_effects(tmp_path):
    db, rec_ids = _db_with_indexed_records(tmp_path)
    (tmp_path / "vector_schema_version").write_text(INDEX_SCHEMA_VERSION, encoding="utf-8")
    assert reset_index_if_schema_upgraded(db) is False
    assert _indexed_flags(db, rec_ids) == [True, True]            # 记录原样


def test_stale_marker_value_triggers_reset(tmp_path):
    db, rec_ids = _db_with_indexed_records(tmp_path)
    (tmp_path / "vector_schema_version").write_text("1", encoding="utf-8")
    assert reset_index_if_schema_upgraded(db) is True
    assert _indexed_flags(db, rec_ids) == [False, False]
    assert (tmp_path / "vector_schema_version").read_text(encoding="utf-8") == INDEX_SCHEMA_VERSION


def test_create_app_runs_schema_reset_on_startup(tmp_path):
    """启动接线:db 就绪后 create_app 内调用重置 —— 旧库(无标记)记录翻回待索引。"""
    db, rec_ids = _db_with_indexed_records(tmp_path)
    create_app(db=db)
    assert _indexed_flags(db, rec_ids) == [False, False]
    assert (tmp_path / "vector_schema_version").read_text(encoding="utf-8") == INDEX_SCHEMA_VERSION


def test_create_app_survives_reset_failure(tmp_path, monkeypatch):
    """重置抛错只记日志,不挡启动(app 可正常服务)。"""
    import epictrace.services.index as idx

    def boom(db):
        raise RuntimeError("reset boom")

    monkeypatch.setattr(idx, "reset_index_if_schema_upgraded", boom)
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    client = TestClient(create_app(db=db))
    assert client.get("/api/health").status_code == 200
