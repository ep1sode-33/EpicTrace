from pathlib import Path

from sqlalchemy import text

from epictrace.config import AppConfig
from epictrace.db import Database


def test_database_session_executes(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    with db.session() as s:
        assert s.execute(text("select 1")).scalar_one() == 1


def test_create_all_migrates_legacy_ingest_records_source_session_id(tmp_path: Path):
    """FIX 1:旧库的 ingest_records 没有 source_session_id 列;create_all() 必须补列,
    否则归类时报 'no such column: source_session_id'。"""
    db = Database(AppConfig(data_dir=tmp_path))
    # 先用裸 SQL 造一个「旧版」ingest_records(故意不含 source_session_id)。
    with db._engine.begin() as conn:  # noqa: SLF001 — 测试直接操作引擎
        conn.exec_driver_sql(
            "CREATE TABLE ingest_records ("
            " id INTEGER PRIMARY KEY,"
            " project_id INTEGER,"
            " original_filename VARCHAR(512),"
            " stored_path VARCHAR(1024),"
            " content_hash VARCHAR(64),"
            " size_bytes INTEGER,"
            " mtime FLOAT,"
            " ingest_method VARCHAR(32),"
            " description TEXT,"
            " extracted_text TEXT,"
            " indexed BOOLEAN,"
            " created_at DATETIME)"
        )
        cols_before = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(ingest_records)")}
    assert "source_session_id" not in cols_before

    db.create_all()  # 应补列(且对已有表的其它列不动)

    with db._engine.begin() as conn:  # noqa: SLF001
        cols_after = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(ingest_records)")}
        assert "source_session_id" in cols_after
        # 能插入带 source_session_id 的行(列真的可用)。
        conn.exec_driver_sql(
            "INSERT INTO ingest_records "
            "(project_id, original_filename, stored_path, content_hash, size_bytes,"
            " mtime, ingest_method, description, extracted_text, indexed, source_session_id) "
            "VALUES (1, 'f', 'p', 'h', 0, 0.0, 'session', '', '', 0, 7)"
        )
        got = conn.exec_driver_sql(
            "SELECT source_session_id FROM ingest_records"
        ).scalar_one()
        assert got == 7
