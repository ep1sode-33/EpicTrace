"""审批挂起-恢复流程测试(需求 7):ApprovalManager + loop before_tool 闸门 + approvals API。"""

import json
import threading
import time

import pytest

from epictrace.cowork.approvals import ApprovalManager
from epictrace.cowork.llm_client import LLMResponse, ToolCall
from epictrace.cowork.loop import AgentLoop
from epictrace.cowork.tools.registry import ToolDef, ToolRegistry
from tests.fakes import FakeCoworkComplete


def test_approval_manager_roundtrip():
    mgr = ApprovalManager()
    req = mgr.request(session_id=1, tool="delete_file", args="{}", allow_session_option=False)
    assert mgr.pending()[0]["tool"] == "delete_file"

    decision_holder = {}

    def waiter():
        decision_holder["d"] = mgr.wait(req["approval_id"], timeout=5)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.1)  # 确保 waiter 先阻塞
    assert mgr.decide(req["approval_id"], "once") is True
    t.join()
    assert decision_holder["d"] == "once"
    assert mgr.pending() == []  # 决策后不再挂起


def test_approval_manager_unknown_id():
    mgr = ApprovalManager()
    assert mgr.decide("nope", "once") is False
    assert mgr.wait("nope", timeout=0.01) is None
    with pytest.raises(ValueError):
        mgr.decide("x", "bogus")


def test_approval_manager_timeout_returns_none():
    mgr = ApprovalManager()
    req = mgr.request(session_id=1, tool="t", args="{}", allow_session_option=True)
    assert mgr.wait(req["approval_id"], timeout=0.05) is None


def _loop_with_gate(decide_fn):
    """构造一个 before_tool 闸门走 decide_fn 的 loop;脚本:调 delete_file → 结束。"""
    r = ToolRegistry()
    r.register(ToolDef(name="delete_file", description="删", parameters={}, handler=lambda: "已删除",
                       permission="ask", always_allow_suppressed=True))
    fake = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="delete_file", arguments="{}")]),
        LLMResponse(content="完毕"),
    ])
    loop = AgentLoop(complete_fn=fake, registry=r, system_prompt="sys", before_tool=decide_fn)
    return loop, fake


def test_loop_gate_deny_skips_execution():
    gate_calls = []

    def gate(tc):
        gate_calls.append(tc["name"])
        return "Error: 用户拒绝了这次工具调用"

    loop, fake = _loop_with_gate(gate)
    out = loop.run([{"role": "user", "content": "hi"}])
    assert gate_calls == ["delete_file"]
    tool_msg = fake.calls[1][0][-1]
    assert "拒绝" in tool_msg["content"]  # 拒绝原因回传给 LLM,不中断循环
    assert out.text == "完毕"


def test_loop_gate_allow_executes():
    loop, fake = _loop_with_gate(lambda tc: None)
    out = loop.run([{"role": "user", "content": "hi"}])
    assert fake.calls[1][0][-1]["content"] == "已删除"
    assert out.text == "完毕"


# ---- API 层:审批端点 + 权限设置 ----

def test_approvals_api(client):
    # 无挂起时为空表
    assert client.get("/api/cowork/approvals").json() == []
    # 未知 id 404
    r = client.post("/api/cowork/approvals/nope", json={"decision": "once"})
    assert r.status_code == 404
    # 手工登记一个挂起请求后,列表可见、决策成功、列表清空
    mgr = client.app.state.cowork_approvals
    req = mgr.request(session_id=1, tool="delete_file", args='{"p":1}',
                      allow_session_option=False)
    pending = client.get("/api/cowork/approvals").json()
    assert pending[0]["approval_id"] == req["approval_id"]
    assert pending[0]["allow_session_option"] is False
    r = client.post(f"/api/cowork/approvals/{req['approval_id']}", json={"decision": "deny"})
    assert r.status_code == 200
    assert client.get("/api/cowork/approvals").json() == []


def test_permission_settings_api(client):
    r = client.get("/api/settings/permissions")
    assert r.json() == {"mode": "ask", "tool_overrides": {}}

    r = client.put("/api/settings/permissions",
                   json={"mode": "skip_all", "tool_overrides": {"delete_*": "ask"}})
    assert r.status_code == 200
    assert r.json()["mode"] == "skip_all"
    assert r.json()["tool_overrides"] == {"delete_*": "ask"}

    r = client.put("/api/settings/permissions", json={"mode": "yolo"})
    assert r.status_code == 400
    r = client.put("/api/settings/permissions", json={"tool_overrides": {"x": "yolo"}})
    assert r.status_code == 400


def test_full_approval_flow_over_sse(client):
    """端到端:LLM 要调 delete_file(ask)→ approval_request 挂起 → POST 批准 → 工具执行 → done。

    注意:starlette TestClient 对同步生成器的 SSE 响应不增量投递(缓冲到结束),
    所以「边读流边决策」不可行——消费放子线程收全文,主线程轮询 approvals 并决策。"""
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    client.app.state.cowork_complete = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="delete_file",
                                         arguments='{"project_id":1,"path":"a.txt"}')]),
        LLMResponse(content="已处理"),
    ])
    body_parts = []

    def consume():
        with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                           json={"content": "删掉它"}) as r:
            for line in r.iter_text():
                body_parts.append(line)

    t = threading.Thread(target=consume)
    t.start()

    approved = False
    for _ in range(200):  # 最多 20s 等挂起请求出现
        pending = client.get("/api/cowork/approvals").json()
        if pending:
            r = client.post(f"/api/cowork/approvals/{pending[0]['approval_id']}",
                            json={"decision": "once"})
            assert r.status_code == 200
            approved = True
            break
        if not t.is_alive():
            break
        time.sleep(0.1)
    t.join(timeout=30)
    assert not t.is_alive(), "SSE 流未在决策后结束"

    body = "".join(body_parts)
    assert approved is True
    assert "approval_request" in body
    assert "approval_resolved" in body
    assert "已处理" in body
    assert "event: done" in body
    # 工具确实被执行(项目不存在,所以是友好的错误回传而非崩溃)
    assert "tool_step" in body


def test_full_deny_flow_over_sse(client):
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    client.app.state.cowork_complete = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="delete_file",
                                         arguments='{"project_id":1,"path":"a.txt"}')]),
        LLMResponse(content="好的,不删了"),
    ])

    body_parts = []

    def consume():
        with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                           json={"content": "删掉它"}) as r:
            for line in r.iter_text():
                body_parts.append(line)

    t = threading.Thread(target=consume)
    t.start()

    denied = False
    for _ in range(200):
        pending = client.get("/api/cowork/approvals").json()
        if pending:
            r = client.post(f"/api/cowork/approvals/{pending[0]['approval_id']}",
                            json={"decision": "deny"})
            assert r.status_code == 200
            denied = True
            break
        if not t.is_alive():
            break
        time.sleep(0.1)
    t.join(timeout=30)
    assert not t.is_alive(), "SSE 流未在决策后结束"

    assert denied is True
    body = "".join(body_parts)
    assert "好的,不删了" in body
    # 拒绝原因写进了 tool 消息(持久化后可查)
    msgs = client.get(f"/api/cowork/sessions/{s['id']}/messages").json()
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert tool_msgs and "拒绝" in tool_msgs[0]["content"]
