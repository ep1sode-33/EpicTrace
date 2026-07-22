"""会话更新(PATCH):改名 + 会话级权限模式;子 agent 项目继承。"""

import threading

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.cowork.agents import AgentDef
from epictrace.cowork.approvals import ApprovalManager
from epictrace.cowork.dispatch import Dispatcher
from epictrace.cowork.llm_client import LLMResponse
from epictrace.cowork.sessions import SessionManager
from epictrace.cowork.tools.registry import ToolDef, ToolRegistry
from tests.fakes import FakeCoworkComplete


def test_patch_permission_mode(client):
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    assert s["permission_mode"] == "ask"
    r = client.patch(f"/api/cowork/sessions/{s['id']}",
                     json={"permission_mode": "skip_all"})
    assert r.status_code == 200
    assert r.json()["permission_mode"] == "skip_all"
    assert client.get(f"/api/cowork/sessions/{s['id']}").json()["permission_mode"] == "skip_all"
    # 非法值 422(schema Literal)
    assert client.patch(f"/api/cowork/sessions/{s['id']}",
                        json={"permission_mode": "yolo"}).status_code == 422
    # 改名与权限模式可同发
    r = client.patch(f"/api/cowork/sessions/{s['id']}",
                     json={"name": "新名字", "permission_mode": "ask"})
    assert r.json()["name"] == "新名字"
    assert r.json()["permission_mode"] == "ask"


def test_child_inherits_parent_project(tmp_path):
    """子 agent 的 project_id 随父会话:在项目树下可见(需求:能力全进项目与对话)。"""
    config = AppConfig(data_dir=tmp_path)
    db = Database(config)
    db.create_all()
    from epictrace.services.projects import ProjectService

    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "p"))
    sessions = SessionManager(db)
    parent = sessions.create(type="agent", project_id=proj.id)
    disp = Dispatcher(
        db=db, sessions=sessions,
        agent_defs={"w": AgentDef(name="w", description="", tools=("echo_tool",))},
        approvals=ApprovalManager(), config=config)
    registry = ToolRegistry()
    registry.register(ToolDef(name="echo_tool", description="", parameters={},
                              handler=lambda: "ok", permission="allow"))
    fake = FakeCoworkComplete([LLMResponse(content="done")])
    disp.start(parent_session_id=parent.id, agent_name="w", task="t",
               registry=registry, complete_factory=lambda model="": fake)
    disp.wait(parent_session_id=parent.id, timeout=10)
    children = sessions.children_of(parent.id)
    assert len(children) == 1
    assert children[0].project_id == proj.id
    # 按项目过滤时子任务也在列
    assert {c.id for c in sessions.list(project_id=proj.id)} == {parent.id, children[0].id}
