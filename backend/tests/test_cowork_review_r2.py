"""codex review R2 回归:turn 串行锁 / admin 收紧压过 session 记忆 / 附件 schema / ask_user SSE。"""

import json
import threading
import time

from epictrace.cowork.llm_client import LLMResponse, ToolCall
from epictrace.cowork.permissions import ASK, PermissionEngine
from epictrace.cowork.tools.registry import ToolDef
from tests.fakes import FakeCoworkComplete


def test_concurrent_turn_rejected(client):
    """同一会话并发 turn:第二个请求被友好拒绝而不是交错执行。"""
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    gate = threading.Event()

    def slow(messages, tools):
        gate.wait(10)
        return LLMResponse(content="慢答")

    client.app.state.cowork_complete = slow
    body1_parts = []

    def first():
        with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                           json={"content": "第一问"}) as r:
            for line in r.iter_text():
                body1_parts.append(line)

    t = threading.Thread(target=first)
    t.start()
    time.sleep(0.5)  # 等第一轮进入 LLM 等待
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "第二问"}) as r:
        body2 = "".join(r.iter_text())
    assert "正在运行中" in body2
    gate.set()
    t.join(timeout=15)
    assert "慢答" in "".join(body1_parts)  # 第一轮不受影响正常完成


def test_admin_tightening_overrides_session_memory(tmp_path):
    """admin 后收紧为 ask 时,已记住的 session 批准不再放行(R2-P2)。"""
    from epictrace.config import AppConfig
    from epictrace.services.settings import SettingsService

    config = AppConfig(data_dir=tmp_path)
    (tmp_path / "admin_policy.json").write_text(
        json.dumps({"tool_policies": {"delete_*": "ask"}}), encoding="utf-8")
    engine = PermissionEngine(SettingsService(config), config)
    tool = ToolDef(name="delete_file", description="", parameters={},
                   handler=lambda: "x", permission="ask")
    d = engine.decide(tool, session_mode="ask", session_approved={"delete_file"})
    assert d.verdict == ASK  # session 记忆不能压过 admin ask


def test_attachment_scalars_use_session_id():
    """附件 collection schema 的归属键是 session_id(旧 conversation_id 已废弃)。"""
    from epictrace.vectorstore.milvus_lite import _ATTACHMENT_SCALARS

    assert "session_id" in _ATTACHMENT_SCALARS
    assert "conversation_id" not in _ATTACHMENT_SCALARS


def test_ask_user_publishes_sse_events(client):
    """ask_user 在活跃流里推 approval_request/resolved SSE(R2-P1)。"""
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    client.app.state.cowork_complete = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="ask_user",
                                         arguments='{"question":"要哪种格式?"}')]),
        LLMResponse(content="明白了"),
    ])
    body_parts = []

    def consume():
        with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                           json={"content": "整理一下"}) as r:
            for line in r.iter_text():
                body_parts.append(line)

    t = threading.Thread(target=consume)
    t.start()
    answered = False
    for _ in range(100):
        pending = client.get("/api/cowork/approvals").json()
        if pending:
            assert pending[0]["kind"] == "question"
            client.post(f"/api/cowork/approvals/{pending[0]['approval_id']}",
                        json={"decision": "Markdown"})
            answered = True
            break
        if not t.is_alive():
            break
        time.sleep(0.1)
    t.join(timeout=15)
    assert answered
    body = "".join(body_parts)
    assert "approval_request" in body
    assert "approval_resolved" in body
    assert "明白了" in body
