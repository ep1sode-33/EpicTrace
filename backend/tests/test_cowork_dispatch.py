"""子 agent 派发测试(需求 4)。

覆盖:YAML 定义加载(捆绑+用户+校验)、start_task 创建 dispatch_child 并行执行、
wait_task 结果回传、工具白名单隔离(子 agent 不可用派发工具/disallowed)、
失败回传不拖垮主 agent、消息按 session 隔离(验收 4)。
"""

import time

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.cowork.agents import AgentDef, load_agent_defs
from epictrace.cowork.approvals import ApprovalManager
from epictrace.cowork.dispatch import Dispatcher
from epictrace.cowork.llm_client import LLMResponse, ToolCall
from epictrace.cowork.sessions import SessionManager
from epictrace.cowork.tools.builtin_dispatch import build_dispatch_tools
from epictrace.cowork.tools.registry import ToolDef, ToolRegistry
from tests.fakes import FakeCoworkComplete


# ---- agents.py:定义加载 ----

def test_load_bundled_defs(tmp_path):
    defs = load_agent_defs(AppConfig(data_dir=tmp_path))
    assert "file-worker" in defs
    fw = defs["file-worker"]
    assert fw.permission_mode == "skip_all"
    assert "delete_file" in fw.disallowed_tools


def test_user_dir_overrides_bundled(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "file-worker.yaml").write_text(
        "name: file-worker\ndescription: 用户覆盖版\nmax_turns: 3\n", encoding="utf-8")
    defs = load_agent_defs(AppConfig(data_dir=tmp_path))
    assert defs["file-worker"].description == "用户覆盖版"
    assert defs["file-worker"].max_turns == 3


def test_invalid_def_skipped(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "bad.yaml").write_text("description: 没名字\n", encoding="utf-8")
    (d / "bad2.yaml").write_text("name: x\npermission_mode: yolo\n", encoding="utf-8")
    (d / "good.yaml").write_text("name: good\ndescription: ok\n", encoding="utf-8")
    defs = load_agent_defs(AppConfig(data_dir=tmp_path))
    assert "bad" not in defs and "x" not in defs
    assert defs["good"].permission_mode == "skip_all"  # 默认无人值守


def test_allowed_tool_names_excludes_dispatch_and_disallowed():
    d = AgentDef(name="a", description="", tools=("read_file", "start_task", "delete_file"),
                 disallowed_tools=("delete_file",))
    names = d.allowed_tool_names(["read_file", "start_task", "wait_task", "delete_file", "search_text"])
    assert names == ["read_file"]
    # 空白名单 = 全部 − disallowed − 派发工具
    d2 = AgentDef(name="b", description="")
    assert d2.allowed_tool_names(["read_file", "start_task", "wait_task"]) == ["read_file"]


# ---- Dispatcher ----

@pytest.fixture()
def env(tmp_path):
    config = AppConfig(data_dir=tmp_path)
    db = Database(config)
    db.create_all()
    sessions = SessionManager(db)
    defs = {
        "worker": AgentDef(name="worker", description="打杂",
                           tools=("echo_tool", "start_task"), max_turns=5),
    }
    disp = Dispatcher(db=db, sessions=sessions, agent_defs=defs,
                      approvals=ApprovalManager(), config=config)
    registry = ToolRegistry()
    registry.register(ToolDef(name="echo_tool", description="回声", parameters={},
                              handler=lambda text="": f"echo:{text}", permission="allow"))
    parent = sessions.create(type="agent")
    return {"config": config, "db": db, "sessions": sessions, "disp": disp,
            "registry": registry, "parent": parent}


def _factory(script):
    fake = FakeCoworkComplete(script)
    return lambda model="": fake, fake


def test_start_and_wait_roundtrip(env):
    fake = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="echo_tool", arguments='{"text":"hi"}')]),
        LLMResponse(content="子任务完成报告"),
    ])
    out = env["disp"].start(parent_session_id=env["parent"].id, agent_name="worker",
                            task="念个咒", registry=env["registry"],
                            complete_factory=lambda model="": fake)
    assert "任务 ID" in out

    result = env["disp"].wait(parent_session_id=env["parent"].id, timeout=10)
    assert "✅ 完成" in result
    assert "子任务完成报告" in result

    # 子 agent 消息隔离在自己的 session(验收 4):user + assistant/tool 都在 child 下
    children = env["sessions"].children_of(env["parent"].id)
    assert len(children) == 1
    child = children[0]
    assert child.type == "dispatch_child"
    assert child.status == "done"
    assert child.config["agent"] == "worker"
    with env["db"].session() as s:
        from sqlalchemy import select

        from epictrace.models import AgentMessage
        msgs = s.execute(select(AgentMessage).where(AgentMessage.session_id == child.id)
                         .order_by(AgentMessage.id)).scalars().all()
    roles = [m.role for m in msgs]
    assert roles[0] == "user" and "assistant" in roles and "tool" in roles
    # 主 session 没有被子 agent 的消息污染
    with env["db"].session() as s:
        parent_msgs = s.execute(select(AgentMessage).where(
            AgentMessage.session_id == env["parent"].id)).scalars().all()
    assert parent_msgs == []

    # 白名单生效:子 agent 的工具 schema 里不应有 start_task
    first_call_schemas = fake.calls[0][1]
    names = [t["function"]["name"] for t in first_call_schemas]
    assert names == ["echo_tool"]


def test_child_whitelist_blocks_tool_call(env):
    """子 agent 试图调白名单外工具 → 错误回传,不执行。"""
    fake = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="start_task", arguments="{}")]),
        LLMResponse(content="好吧"),
    ])
    env["disp"].start(parent_session_id=env["parent"].id, agent_name="worker",
                      task="t", registry=env["registry"],
                      complete_factory=lambda model="": fake)
    env["disp"].wait(parent_session_id=env["parent"].id, timeout=10)
    tool_msg = fake.calls[1][0][-1]
    assert "not available" in tool_msg["content"]


def test_unknown_agent_is_error(env):
    out = env["disp"].start(parent_session_id=env["parent"].id, agent_name="ghost",
                            task="t", registry=env["registry"],
                            complete_factory=lambda model="": FakeCoworkComplete())
    assert out.startswith("Error: 未知子 agent")
    assert env["sessions"].children_of(env["parent"].id) == []


def test_parallel_children_and_failure_isolation(env):
    """两个子 agent 并行;一个崩了不影响另一个,失败以文本回传(验收 1 的拆解-汇总路径)。"""
    ok_fake = FakeCoworkComplete([LLMResponse(content="甲好")])
    bad_fake = FakeCoworkComplete()  # 会被换成抛错件

    def raising(model=""):
        def boom(messages, tools):
            raise RuntimeError("llm boom")
        return boom

    env["disp"].start(parent_session_id=env["parent"].id, agent_name="worker",
                      task="任务甲", registry=env["registry"],
                      complete_factory=lambda model="": ok_fake)
    env["disp"].start(parent_session_id=env["parent"].id, agent_name="worker",
                      task="任务乙", registry=env["registry"],
                      complete_factory=raising)
    result = env["disp"].wait(parent_session_id=env["parent"].id, timeout=10)
    assert "✅ 完成" in result and "甲好" in result
    assert "❌ 失败" in result and "llm boom" in result

    children = env["sessions"].children_of(env["parent"].id)
    assert {c.status for c in children} == {"done", "error"}


def test_wait_without_tasks(env):
    assert "没有进行中的子任务" in env["disp"].wait(parent_session_id=env["parent"].id)


def test_children_progress(env):
    fake = FakeCoworkComplete([LLMResponse(content="done")])
    env["disp"].start(parent_session_id=env["parent"].id, agent_name="worker",
                      task="t", registry=env["registry"],
                      complete_factory=lambda model="": fake)
    env["disp"].wait(parent_session_id=env["parent"].id, timeout=10)
    p = env["disp"].children_progress(env["parent"].id)
    assert p == {"total": 1, "done": 1, "running": 0}


# ---- 派发工具(handler 线程模型:start 立即返回,wait 阻塞收集) ----

def test_dispatch_tools_handlers(env):
    fake = FakeCoworkComplete([LLMResponse(content="打杂完毕")])
    tools = build_dispatch_tools(env["disp"], parent_session_id=env["parent"].id,
                                 registry=env["registry"],
                                 complete_factory=lambda model="": fake)
    by_name = {t.name: t for t in tools}
    assert by_name["start_task"].always_allow_suppressed is True  # 需求 7
    assert by_name["wait_task"].permission == "allow"

    t0 = time.time()
    out = env["registry"]  # 注册后通过 registry 执行,走同一错误回传路径
    for t in tools:
        out.register(t)
    r = out.execute("start_task", '{"agent":"worker","task":"扫地"}')
    assert "任务 ID" in r
    assert time.time() - t0 < 2  # start 不阻塞等子 agent 跑完
    r2 = out.execute("wait_task", "{}")
    assert "打杂完毕" in r2
