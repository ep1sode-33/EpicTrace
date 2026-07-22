"""旧对话栈 → cowork 栈的一次性数据迁移(epictrace.db_migrate)测试。

旧 ORM 模型已删除,这里用原生 SQL 手工建旧表 + 插样本数据,再触发 create_all(内部
末尾调 migrate_legacy_conversations),断言搬迁结果与幂等性。
"""
from __future__ import annotations

import json
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Base, Database
from epictrace.models import AgentMessage, AgentSession, Project, Reference

_TS = "2026-01-02 03:04:05.000000"


def _orm_tables_with_project(db: Database) -> int:
    """先建好新栈全部表并插一个项目(模拟「升级前已有项目」的库),返回 project_id。"""
    from epictrace import models  # noqa: F401 — 确保全部 model 已注册

    Base.metadata.create_all(db._engine)  # noqa: SLF001
    with db.session() as s:
        p = Project(title="P", folder_path="/tmp/P"); s.add(p); s.flush()
        return p.id


def _create_legacy_schema(db: Database) -> None:
    """按旧模型的形状建 conversations/messages/conversation_references 三张旧表。"""
    with db._engine.begin() as conn:  # noqa: SLF001 — 测试直接操作引擎
        conn.exec_driver_sql(
            "CREATE TABLE conversations ("
            " id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL,"
            " title VARCHAR(255) NOT NULL DEFAULT '新对话',"
            " created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE messages ("
            " id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL,"
            " role VARCHAR(16) NOT NULL, content TEXT NOT NULL DEFAULT '',"
            " citations_json TEXT, created_at DATETIME NOT NULL)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE conversation_references ("
            " id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL,"
            " kind VARCHAR(16) NOT NULL, display_name VARCHAR(512) NOT NULL,"
            " source_path VARCHAR(1024), ingest_record_id INTEGER,"
            " extracted_text TEXT, text_chars INTEGER NOT NULL DEFAULT 0,"
            " mode VARCHAR(16) NOT NULL, detached BOOLEAN NOT NULL DEFAULT 0,"
            " created_at DATETIME NOT NULL)"
        )


def _seed_legacy(db: Database, project_id: int) -> dict:
    """插两个会话:c1 挂在项目下(2 消息 + 1 引用),c2 空会话。返回旧 id 集。"""
    cites = json.dumps([{"n": 1, "ingest_record_id": 7, "snippet": "页表"}],
                       ensure_ascii=False)
    with db._engine.begin() as conn:  # noqa: SLF001
        cur = conn.exec_driver_sql(
            "INSERT INTO conversations (project_id, title, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)", (project_id, "问页表", _TS, _TS))
        c1 = cur.lastrowid
        cur = conn.exec_driver_sql(
            "INSERT INTO conversations (project_id, title, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)", (project_id, "空会话", _TS, _TS))
        c2 = cur.lastrowid
        conn.exec_driver_sql(
            "INSERT INTO messages (conversation_id, role, content, citations_json, created_at)"
            " VALUES (?, 'user', '页表是啥', NULL, ?)", (c1, _TS))
        conn.exec_driver_sql(
            "INSERT INTO messages (conversation_id, role, content, citations_json, created_at)"
            " VALUES (?, 'assistant', '答[1]', ?, ?)", (c1, cites, _TS))
        conn.exec_driver_sql(
            "INSERT INTO conversation_references (conversation_id, kind, display_name,"
            " source_path, ingest_record_id, extracted_text, text_chars, mode, detached,"
            " created_at)"
            " VALUES (?, 'external', 'note.md', '/tmp/note.md', NULL, '页表内容', 4,"
            " 'fulltext', 0, ?)", (c1, _TS))
    return {"c1": c1, "c2": c2, "cites": cites}


def _table_names(db: Database) -> set[str]:
    with db._engine.connect() as conn:  # noqa: SLF001
        return {r[0] for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'")}


def test_migrates_legacy_tables_into_cowork_stack(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    pid = _orm_tables_with_project(db)
    _create_legacy_schema(db)
    seeded = _seed_legacy(db, pid)

    db.create_all()  # 末尾触发迁移

    with db.session() as s:
        sessions = s.query(AgentSession).order_by(AgentSession.id).all()
        assert len(sessions) == 2
        by_name = {sess.name: sess for sess in sessions}
        s1 = by_name["问页表"]
        assert s1.type == "agent" and s1.status == "idle" and s1.permission_mode == "ask"
        assert s1.project_id == pid and s1.config == {}
        assert by_name["空会话"].project_id == pid

        msgs = s.query(AgentMessage).filter_by(session_id=s1.id).order_by(AgentMessage.id).all()
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[0].content == "页表是啥" and msgs[0].citations_json is None
        assert msgs[1].citations_json == seeded["cites"]      # 引用链原样保留
        assert msgs[1].tool_call_id is None and msgs[1].tool_calls_json is None

        refs = s.query(Reference).filter_by(session_id=s1.id).all()
        assert len(refs) == 1
        r = refs[0]
        assert (r.kind, r.display_name, r.source_path, r.mode) == (
            "external", "note.md", "/tmp/note.md", "fulltext")
        assert r.extracted_text == "页表内容" and r.text_chars == 4 and r.detached is False

    tables = _table_names(db)
    assert "conversations" not in tables and "messages" not in tables
    assert "conversation_references" not in tables


def test_migration_backs_up_db_file(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    _create_legacy_schema(db)
    db.create_all()
    assert (tmp_path / "epictrace.db.premigrate.bak").exists()


def test_migration_is_idempotent_noop_without_legacy_tables(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    pid = _orm_tables_with_project(db)
    _create_legacy_schema(db)
    _seed_legacy(db, pid)

    db.create_all()
    db.create_all()  # 第二次:旧表已无 → no-op,不得重复搬迁

    with db.session() as s:
        assert s.query(AgentSession).count() == 2
        assert s.query(AgentMessage).count() == 2
        assert s.query(Reference).count() == 1


def test_fresh_db_has_no_backup_and_no_side_effects(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    assert not (tmp_path / "epictrace.db.premigrate.bak").exists()
    with db.session() as s:
        assert s.query(AgentSession).count() == 0
