"""codex review P2:级联删除(项目→会话、父会话→子 agent、会话→引用)。"""

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import AgentMessage, AgentSession, Reference
from epictrace.cowork.sessions import SessionManager


@pytest.fixture()
def env(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    from epictrace.services.projects import ProjectService

    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "p"))
    sessions = SessionManager(db)
    parent = sessions.create(type="agent", project_id=proj.id)
    child = sessions.create(type="dispatch_child", parent_id=parent.id, project_id=proj.id)
    with db.session() as s:
        s.add(AgentMessage(session_id=parent.id, role="user", content="hi"))
        s.add(AgentMessage(session_id=child.id, role="user", content="t"))
        s.add(Reference(session_id=parent.id, kind="external", display_name="a",
                        text_chars=1, mode="fulltext"))
    return {"db": db, "proj": proj, "sessions": sessions, "parent": parent, "child": child}


def _counts(db):
    from sqlalchemy import select, func

    with db.session() as s:
        return (
            s.execute(select(func.count(AgentSession.id))).scalar(),
            s.execute(select(func.count(AgentMessage.id))).scalar(),
            s.execute(select(func.count(Reference.id))).scalar(),
        )


def test_delete_parent_cascades_children_and_references(env):
    assert env["sessions"].delete(env["parent"].id) is True
    assert _counts(env["db"]) == (0, 0, 0)


def test_delete_project_cascades_sessions(env):
    from epictrace.services.projects import ProjectService

    ProjectService(env["db"]).delete(env["proj"].id)
    assert _counts(env["db"]) == (0, 0, 0)


def test_delete_child_alone_keeps_parent(env):
    assert env["sessions"].delete(env["child"].id) is True
    sessions_count, messages_count, refs_count = _counts(env["db"])
    assert sessions_count == 1  # parent 还在
    assert messages_count == 1  # 只剩 parent 的消息
    assert refs_count == 1
