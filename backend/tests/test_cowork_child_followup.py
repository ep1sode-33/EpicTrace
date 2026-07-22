"""codex review P1:dispatch_child 续聊恢复子 agent 白名单(不变成全工具会话)。"""

from epictrace.cowork.llm_client import LLMResponse
from tests.fakes import FakeCoworkComplete


def test_dispatch_child_followup_keeps_whitelist(client):
    """用户对子 agent session 续聊:工具 schema 仍是该子 agent 定义的白名单。"""
    parent = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    fake = FakeCoworkComplete([LLMResponse(content="子任务完成")])
    client.app.state.cowork_complete = fake
    # 经 SSE 派发一个 file-worker 子任务
    from epictrace.cowork.llm_client import ToolCall

    fake._script.insert(0, LLMResponse(
        tool_calls=[ToolCall(id="t1", name="start_task",
                             arguments='{"agent":"file-worker","task":"读个文件"}')]))
    fake._script.insert(1, LLMResponse(
        tool_calls=[ToolCall(id="t2", name="wait_task", arguments="{}")]))
    fake._script.append(LLMResponse(content="汇总"))
    parent2 = client.post("/api/cowork/sessions",
                          json={"type": "agent", "permission_mode": "skip_all"}).json()
    with client.stream("POST", f"/api/cowork/sessions/{parent2['id']}/messages",
                       json={"content": "处理一下"}) as r:
        "".join(r.iter_text())
    children = [x for x in client.get("/api/cowork/sessions").json()
                if x["parent_id"] == parent2["id"]]
    assert len(children) == 1
    child = children[0]

    # 对子 agent 续聊:fake 记录 schema;内容应只含 file-worker 白名单工具
    calls_before = len(fake.calls)
    with client.stream("POST", f"/api/cowork/sessions/{child['id']}/messages",
                       json={"content": "继续"}) as r:
        "".join(r.iter_text())
    schemas = fake.calls[calls_before][1]
    names = {t["function"]["name"] for t in schemas}
    assert names <= {"list_projects", "list_files", "read_file", "search_text",
                     "search_vector", "search_hybrid", "get_timestamp_citation"}
    assert "start_task" not in names
    assert "run_bash" not in names
    assert "delete_file" not in names
    # system prompt 仍是 dispatch_child 身份(不是主 agent)
    assert "由主 agent 派发" in fake.calls[calls_before][0][0]["content"]
