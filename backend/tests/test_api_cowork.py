"""Cowork API 单测:session CRUD / SSE 消息流 / 工具清单 / agent 设置。"""

from epictrace.cowork.llm_client import LLMResponse, ToolCall
from tests.fakes import FakeCoworkComplete


def _create_session(client, **kw):
    payload = {"type": "agent"}
    payload.update(kw)
    r = client.post("/api/cowork/sessions", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_session_crud(client):
    s = _create_session(client, name="主会话")
    assert s["status"] == "idle" and s["permission_mode"] == "ask"

    r = client.get("/api/cowork/sessions")
    assert any(x["id"] == s["id"] for x in r.json())

    r = client.get(f"/api/cowork/sessions/{s['id']}")
    assert r.json()["name"] == "主会话"

    r = client.get("/api/cowork/sessions/9999")
    assert r.status_code == 404

    r = client.delete(f"/api/cowork/sessions/{s['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/cowork/sessions/{s['id']}").status_code == 404


def test_create_session_validates_type(client):
    r = client.post("/api/cowork/sessions", json={"type": "bogus"})
    assert r.status_code == 422


def test_send_message_sse_flow(client):
    s = _create_session(client)
    client.app.state.cowork_complete = FakeCoworkComplete([
        LLMResponse(reasoning="想一下",
                    tool_calls=[ToolCall(id="c1", name="list_projects", arguments="{}")]),
        LLMResponse(content="这是答复"),
    ])
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "看看项目"}) as r:
        body = "".join(r.iter_text())
    assert r.status_code == 200
    assert "event: token" in body
    assert "这是答复" in body
    assert "event: done" in body
    assert "event: session_state" in body
    assert "event: tool_step" in body

    # 消息持久化:user + assistant(tool_calls) + tool + assistant
    r = client.get(f"/api/cowork/sessions/{s['id']}/messages")
    roles = [m["role"] for m in r.json()]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert r.json()[2]["name"] == "list_projects"


def test_send_message_unknown_session_404(client):
    r = client.post("/api/cowork/sessions/9999/messages", json={"content": "hi"})
    assert r.status_code == 404


def test_send_message_llm_error_event(client):
    s = _create_session(client)

    def broken(_m, _t):
        raise RuntimeError("LLM 挂了")

    client.app.state.cowork_complete = broken
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "hi"}) as r:
        body = "".join(r.iter_text())
    assert "event: error" in body
    assert "LLM 挂了" in body
    # 错误后 session 回到 idle
    assert client.get(f"/api/cowork/sessions/{s['id']}").json()["status"] == "idle"


def test_chat_session_gets_no_tools(client):
    s = _create_session(client, type="chat")
    fake = FakeCoworkComplete([LLMResponse(content="纯聊天答复")])
    client.app.state.cowork_complete = fake
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "hi"}) as r:
        body = "".join(r.iter_text())
    assert "纯聊天答复" in body
    # chat 类型不下发工具 schema
    assert fake.calls[0][1] == []


def test_tools_listing(client):
    r = client.get("/api/cowork/tools")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()}
    assert {"list_projects", "list_files", "read_file", "search_text",
            "search_vector", "search_hybrid", "get_timestamp_citation"} <= names
    for t in r.json():
        assert t["permission"] in ("ask", "ask-session", "allow")
        assert t["sandbox"] in ("never", "optional", "required")


def test_agent_settings_defaults_and_update(client):
    r = client.get("/api/settings/agent")
    assert r.status_code == 200
    assert r.json() == {"max_turns": 50, "turn_timeout_sec": 120, "user_instructions": ""}

    r = client.put("/api/settings/agent", json={"max_turns": 20, "user_instructions": "用中文"})
    assert r.status_code == 200
    assert r.json()["max_turns"] == 20
    assert r.json()["user_instructions"] == "用中文"
    # 未带的键保留
    assert r.json()["turn_timeout_sec"] == 120

    r = client.put("/api/settings/agent", json={"max_turns": 0})
    assert r.status_code == 400
    r = client.put("/api/settings/agent", json={"turn_timeout_sec": 99999})
    assert r.status_code == 400


def test_user_instructions_injected_into_prompt(client):
    client.put("/api/settings/agent", json={"user_instructions": "总是用中文回答"})
    s = _create_session(client)
    fake = FakeCoworkComplete([LLMResponse(content="好")])
    client.app.state.cowork_complete = fake
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "hi"}) as r:
        "".join(r.iter_text())
    system_msg = fake.calls[0][0][0]
    assert system_msg["role"] == "system"
    assert "总是用中文回答" in system_msg["content"]
    assert "# 身份" in system_msg["content"]  # 分节组装证据(验收 2)


# ---- 子 agent 派发 e2e(需求 4,验收 1/4)----

class _RoutingFake:
    """主/子 agent 共用一个注入 complete_fn:按 system prompt 区分角色。
    主 agent 走脚本;子 agent 直接交结果,并记录收到的工具 schema(白名单断言)。"""

    def __init__(self, main_script):
        self._main_script = list(main_script)
        self.child_tool_names: list[str] = []
        self.main_system: str = ""

    def __call__(self, messages, tools):
        if "由主 agent 派发" in (messages[0]["content"] or ""):  # dispatch_child 的 identity 节
            self.child_tool_names = [t["function"]["name"] for t in tools]
            return LLMResponse(content="子结果:三份 PDF 的要点摘要")
        if not self.main_system:
            self.main_system = messages[0]["content"] or ""
        return self._main_script.pop(0)


def test_agents_listing(client):
    r = client.get("/api/cowork/agents")
    assert r.status_code == 200
    names = {a["name"] for a in r.json()}
    assert "file-worker" in names


def test_dispatch_flow_over_sse(client):
    """主 agent 经 start_task 派发 file-worker,wait_task 收结果后汇总(验收 1 的最小闭环)。"""
    s = _create_session(client, permission_mode="skip_all")  # skip_all:start_task 无需逐次确认
    fake = _RoutingFake([
        LLMResponse(tool_calls=[ToolCall(id="t1", name="start_task",
                                         arguments='{"agent":"file-worker","task":"总结项目 1 的三份 PDF"}')]),
        LLMResponse(tool_calls=[ToolCall(id="t2", name="wait_task", arguments="{}")]),
        LLMResponse(content="汇总:三份 PDF 的核心结论如下……"),
    ])
    client.app.state.cowork_complete = fake

    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "处理这三份 PDF 并写一份中文摘要"}) as r:
        body = "".join(r.iter_text())
    assert "event: done" in body
    assert "汇总:三份 PDF" in body

    # 子 agent session 隔离存在,状态 done(验收 4)
    children = [x for x in client.get("/api/cowork/sessions").json()
                if x["parent_id"] == s["id"]]
    assert len(children) == 1
    assert children[0]["type"] == "dispatch_child"
    assert children[0]["status"] == "done"

    # 子 agent 工具白名单:无派发工具、无 delete_file
    assert "start_task" not in fake.child_tool_names
    assert "wait_task" not in fake.child_tool_names
    assert "delete_file" not in fake.child_tool_names
    assert "read_file" in fake.child_tool_names

    # 进度端点(需求 10)
    prog = client.get(f"/api/cowork/sessions/{s['id']}/progress").json()
    assert prog == {"total": 1, "done": 1, "running": 0}

    # 主 agent 的 system prompt 含 dispatch 节与可用子 agent 列表(需求 2/4)
    assert "# 子 agent 派发" in fake.main_system
    assert "file-worker" in fake.main_system


# ---- Skill 注入(需求 6,验收 7)----

def test_skills_listing(client):
    r = client.get("/api/cowork/skills")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert {"pdf-reading", "docx", "pptx"} <= names
    assert all(s["source"] in ("bundled", "user") for s in r.json())


def test_skills_injected_into_main_prompt(client):
    s = _create_session(client)
    fake = FakeCoworkComplete([LLMResponse(content="好")])
    client.app.state.cowork_complete = fake
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "hi"}) as r:
        "".join(r.iter_text())
    system_msg = fake.calls[0][0][0]["content"]
    assert "# 已加载 Skills" in system_msg
    assert "pdf-reading" in system_msg  # 捆绑 skill 正文注入(验收 7 的 prompt 侧证据)
