"""一次性数据迁移:旧对话栈(conversations / messages / conversation_references)→ cowork 栈
(agent_sessions / agent_messages / references)。

在 Database.create_all() 末尾调用(旧 ORM 模型已删除,故全程 exec_driver_sql 原生 SQL)。
检测:sqlite_master 存在 conversations 表 → 迁移;否则 no-op(幂等)。迁移前把 sqlite 库文件
复制为 <db文件>.premigrate.bak(已存在则覆盖;:memory:/文件缺失时静默跳过)。

已知取舍:会话级附件向量(milvus attachment 集合,旧 metadata 键 conversation_id)**不迁移**——
旧向量成为孤儿(检索按活跃 references 过滤,不会被命中);附件的 extracted_text 已随
conversation_references 迁入 references,deferred/大文件可重建会话级索引,影响可控。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy.engine import Engine

_log = logging.getLogger("epictrace.db")

_TABLES = ("conversations", "messages", "conversation_references")


def _table_names(conn) -> set[str]:
    rows = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
    return {r[0] for r in rows}


def _backup(engine: Engine) -> None:
    """迁移前备份库文件为 <db>.premigrate.bak(覆盖既有);:memory:/文件不存在 → 静默跳过。"""
    db = engine.url.database
    if not db or db == ":memory:":
        return
    src = Path(db)
    if not src.exists():
        return
    dst = src.with_name(src.name + ".premigrate.bak")
    shutil.copyfile(src, dst)
    _log.info("迁移前备份:%s → %s", src, dst)


def migrate_legacy_conversations(engine: Engine) -> None:
    """检测并搬迁旧对话栈数据,随后 drop 旧表。无旧表 → no-op(幂等)。"""
    with engine.connect() as conn:
        tables = _table_names(conn)
    if "conversations" not in tables:
        return

    _backup(engine)

    with engine.begin() as conn:
        # conversations → agent_sessions(记旧 conv id → 新 session id 映射)
        id_map: dict[int, int] = {}
        conv_rows = conn.exec_driver_sql(
            "SELECT id, project_id, title, created_at, updated_at FROM conversations ORDER BY id"
        ).all()
        for cid, project_id, title, created_at, updated_at in conv_rows:
            cur = conn.exec_driver_sql(
                "INSERT INTO agent_sessions"
                " (type, parent_id, project_id, name, status, permission_mode, config,"
                "  created_at, updated_at)"
                " VALUES ('agent', NULL, ?, ?, 'idle', 'ask', '{}', ?, ?)",
                (project_id, title, created_at, updated_at),
            )
            id_map[cid] = cur.lastrowid

        # messages → agent_messages(tool 相关列为旧栈所无,置 NULL)
        n_msg = 0
        if "messages" in tables:
            msg_rows = conn.exec_driver_sql(
                "SELECT conversation_id, role, content, citations_json, created_at"
                " FROM messages ORDER BY id"
            ).all()
            for cid, role, content, citations_json, created_at in msg_rows:
                sid = id_map.get(cid)
                if sid is None:
                    continue  # 孤儿消息(会话已被删):跳过
                conn.exec_driver_sql(
                    "INSERT INTO agent_messages"
                    " (session_id, role, content, name, tool_call_id, tool_calls_json,"
                    "  citations_json, created_at)"
                    " VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?)",
                    (sid, role, content, citations_json, created_at),
                )
                n_msg += 1

        # conversation_references → references("references" 是 SQL 关键字,原生 SQL 里加引号)。
        # 保留旧 id:citations_json 里的 reference_id 指向它(attachment 引用跳回命门),
        # 重新分配会让历史附件引用跳错文件(codex review P1)。
        n_ref = 0
        if "conversation_references" in tables:
            ref_rows = conn.exec_driver_sql(
                "SELECT id, conversation_id, kind, display_name, source_path, ingest_record_id,"
                " extracted_text, text_chars, mode, detached, created_at"
                " FROM conversation_references ORDER BY id"
            ).all()
            for (rid, cid, kind, display_name, source_path, ingest_record_id, extracted_text,
                 text_chars, mode, detached, created_at) in ref_rows:
                sid = id_map.get(cid)
                if sid is None:
                    continue
                conn.exec_driver_sql(
                    'INSERT INTO "references"'
                    " (id, session_id, kind, display_name, source_path, ingest_record_id,"
                    "  extracted_text, text_chars, mode, detached, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, sid, kind, display_name, source_path, ingest_record_id,
                     extracted_text, text_chars, mode, detached, created_at),
                )
                n_ref += 1

        for table in _TABLES:
            if table in tables:
                conn.exec_driver_sql(f"DROP TABLE {table}")

    _log.info(
        "旧对话栈迁移完成:conversations %d → agent_sessions,messages %d → agent_messages,"
        "conversation_references %d → references(旧表已 drop)",
        len(id_map), n_msg, n_ref,
    )
