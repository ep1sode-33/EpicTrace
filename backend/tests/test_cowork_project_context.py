"""codex review P1:项目绑定会话把 project_id 注入 agent 上下文。"""

from epictrace.cowork.llm_client import LLMResponse
from tests.fakes import FakeCoworkComplete


def test_project_bound_session_injects_project(client):
    proj = client.post("/api/projects",
                       json={"title": "课程项目", "folder_path": "/tmp/x"}).json()
    s = client.post("/api/cowork/sessions",
                    json={"type": "agent", "project_id": proj["id"]}).json()
    fake = FakeCoworkComplete([LLMResponse(content="好")])
    client.app.state.cowork_complete = fake
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "总结一下这个项目"}) as r:
        "".join(r.iter_text())
    system = fake.calls[0][0][0]["content"]
    assert f"id={proj['id']}" in system
    assert "课程项目" in system


def test_unbound_session_has_no_project_line(client):
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    fake = FakeCoworkComplete([LLMResponse(content="好")])
    client.app.state.cowork_complete = fake
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "hi"}) as r:
        "".join(r.iter_text())
    system = fake.calls[0][0][0]["content"]
    assert "当前项目" not in system
