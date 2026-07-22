"""codex review P1 修复的回归测试:loop 超时口径 + 失败轮步骤落库。"""

import threading
import time

from epictrace.cowork.llm_client import LLMResponse, ToolCall
from epictrace.cowork.loop import AgentLoop, AgentLoopError
from epictrace.cowork.tools.registry import ToolDef, ToolRegistry
from tests.fakes import FakeCoworkComplete


def test_slow_tool_not_killed_by_turn_timeout():
    """工具执行 2s(超出 turn_timeout=1s)不再误杀:turn 预算只计 LLM 调用本身。"""
    r = ToolRegistry()
    r.register(ToolDef(
        name="slow", description="", parameters={},
        handler=lambda: (time.sleep(2), "慢结果")[1], permission="allow"))
    fake = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="slow", arguments="{}")]),
        LLMResponse(content="完成"),
    ])
    loop = AgentLoop(complete_fn=fake, registry=r, system_prompt="s", turn_timeout=1.0)
    out = loop.run([{"role": "user", "content": "hi"}])
    assert out.text == "完成"


def test_failed_turn_keeps_partial_messages():
    """第二轮 LLM 崩了:已执行的工具步骤在 e.partial 里,不丢。"""
    r = ToolRegistry()
    r.register(ToolDef(name="echo", description="", parameters={},
                       handler=lambda: "回", permission="allow"))

    calls = {"n": 0}

    def flaky(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(tool_calls=[ToolCall(id="c1", name="echo", arguments="{}")])
        raise RuntimeError("llm boom")

    loop = AgentLoop(complete_fn=flaky, registry=r, system_prompt="s")
    try:
        loop.run([{"role": "user", "content": "hi"}])
        raise AssertionError("应抛 AgentLoopError")
    except AgentLoopError as e:
        roles = [m["role"] for m in e.partial]
        assert roles == ["assistant", "tool"]
        assert e.partial[1]["content"] == "回"


def test_partial_persisted_on_sse_error(client):
    """e2e:工具执行后 LLM 崩 → 错误事件之外,tool/assistant 步骤已落库可查。"""
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()

    calls = {"n": 0}

    def flaky(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return LLMResponse(tool_calls=[ToolCall(id="c1", name="list_projects", arguments="{}")])
        raise RuntimeError("llm boom")

    client.app.state.cowork_complete = flaky
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "hi"}) as r:
        body = "".join(r.iter_text())
    assert "event: error" in body
    msgs = client.get(f"/api/cowork/sessions/{s['id']}/messages").json()
    roles = [m["role"] for m in msgs]
    assert "assistant" in roles and "tool" in roles  # 副作用步骤留痕


# ---- codex review P1:Stop 取消链路 ----

def test_should_stop_aborts_between_tools():
    """取消信号在工具之间生效:第二个工具不再执行,partial 留痕。"""
    r = ToolRegistry()
    ran = []
    r.register(ToolDef(name="t1", description="", parameters={},
                       handler=lambda: ran.append("t1") or "1", permission="allow"))
    r.register(ToolDef(name="t2", description="", parameters={},
                       handler=lambda: ran.append("t2") or "2", permission="allow"))
    fake = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="a", name="t1", arguments="{}"),
                                ToolCall(id="b", name="t2", arguments="{}")]),
    ])
    state = {"stop": False}

    def mark_stop(**_):
        ran.append("t1")
        state["stop"] = True
        return "1"

    r._tools["t1"] = ToolDef(name="t1", description="", parameters={},
                             handler=mark_stop, permission="allow")
    loop = AgentLoop(complete_fn=fake, registry=r, system_prompt="s",
                     should_stop=lambda: state["stop"])
    try:
        loop.run([{"role": "user", "content": "hi"}])
        raise AssertionError("应因取消而抛错")
    except AgentLoopError as e:
        assert "已被用户停止" in str(e)
        assert ran == ["t1"]  # t2 未执行
        assert any(m["role"] == "tool" for m in e.partial)


def test_approval_wait_wakes_on_cancel():
    import threading
    import time

    from epictrace.cowork.approvals import ApprovalManager

    mgr = ApprovalManager()
    req = mgr.request(session_id=1, tool="t", args="{}", allow_session_option=False)
    cancel = threading.Event()
    out = {}

    def waiter():
        out["d"] = mgr.wait(req["approval_id"], timeout=30, cancel=cancel)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.2)
    cancel.set()
    t.join(timeout=5)
    assert not t.is_alive()  # 没有睡满 30s
    assert out["d"] is None


def test_stop_endpoint(client):
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    r = client.post(f"/api/cowork/sessions/{s['id']}/stop")
    assert r.status_code == 200
    assert client.app.state.cowork_cancels[s["id"]].is_set()
    assert client.post("/api/cowork/sessions/9999/stop").status_code == 404
