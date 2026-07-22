"""SessionManager 单测(需求 9):类型/状态机/层级/删除级联。"""

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.cowork.sessions import SessionManager


@pytest.fixture()
def mgr(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    return SessionManager(db)


def test_create_defaults(mgr):
    s = mgr.create()
    assert s.type == "agent"
    assert s.status == "idle"
    assert s.permission_mode == "ask"
    assert s.parent_id is None


def test_create_validates_type_and_mode(mgr):
    with pytest.raises(ValueError, match="session type"):
        mgr.create(type="bogus")
    with pytest.raises(ValueError, match="permission_mode"):
        mgr.create(permission_mode="yolo")


def test_all_five_types_accepted(mgr):
    for t in ("agent", "dispatch_child", "scheduled", "chat", "radar"):
        assert mgr.create(type=t).type == t


def test_list_and_get(mgr):
    a = mgr.create(name="甲")
    b = mgr.create(type="chat", name="乙")
    ids = {s.id for s in mgr.list()}
    assert {a.id, b.id} <= ids
    assert mgr.get(a.id).name == "甲"
    assert mgr.get(9999) is None


def test_parent_child(mgr):
    parent = mgr.create()
    child = mgr.create(type="dispatch_child", parent_id=parent.id, name="子任务")
    assert [c.id for c in mgr.children_of(parent.id)] == [child.id]
    # include_children=False 只看顶层
    top = mgr.list(include_children=False)
    assert {s.id for s in top} == {parent.id}


def test_state_machine(mgr):
    s = mgr.create()
    for state in ("thinking", "executing", "waiting_approval", "done", "idle"):
        mgr.set_state(s.id, state)
        assert mgr.get(s.id).status == state
    with pytest.raises(ValueError, match="state"):
        mgr.set_state(s.id, "flying")


def test_delete_cascades_messages(mgr):
    from epictrace.models import AgentMessage

    s = mgr.create()
    with mgr._db.session() as db:
        db.add(AgentMessage(session_id=s.id, role="user", content="hi"))
    assert mgr.delete(s.id) is True
    assert mgr.get(s.id) is None
    with mgr._db.session() as db:
        from sqlalchemy import select

        left = db.execute(select(AgentMessage).where(AgentMessage.session_id == s.id)).scalars().all()
    assert left == []
    assert mgr.delete(s.id) is False
