"""Phase B:消息编辑/重生成 + 首轮自动标题(语义对齐旧 ChatService)。"""

from epictrace.cowork.llm_client import LLMResponse
from tests.fakes import FakeCoworkComplete


def _create_session(client, **kw):
    payload = {"type": "agent"}
    payload.update(kw)
    return client.post("/api/cowork/sessions", json=payload).json()


def _send(client, sid, content="hi"):
    with client.stream("POST", f"/api/cowork/sessions/{sid}/messages",
                       json={"content": content}) as r:
        return "".join(r.iter_text())


def test_auto_title_on_first_turn(client):
    s = _create_session(client)
    assert s["name"] in (None, "")
    fake = FakeCoworkComplete([LLMResponse(content="预算的答案"), LLMResponse(content="预算问答")])
    client.app.state.cowork_complete = fake
    _send(client, s["id"], "第三季度预算是多少")
    # 标题调用是第二轮 complete(第一轮是回答)
    assert len(fake.calls) == 2
    title_msgs = fake.calls[1][0]
    assert "标题生成器" in title_msgs[0]["content"]
    updated = client.get(f"/api/cowork/sessions/{s['id']}").json()
    assert updated["name"] == "预算问答"


def test_no_title_when_named_or_not_first(client):
    s = _create_session(client, name="已命名")
    fake = FakeCoworkComplete([LLMResponse(content="答")])
    client.app.state.cowork_complete = fake
    _send(client, s["id"], "问")
    assert len(fake.calls) == 1  # 不触发标题调用
    assert client.get(f"/api/cowork/sessions/{s['id']}").json()["name"] == "已命名"


def test_regenerate_replaces_last_turn(client):
    s = _create_session(client)
    client.app.state.cowork_complete = FakeCoworkComplete([
        LLMResponse(content="第一版"),
        LLMResponse(content="标题"),  # 首轮标题
        LLMResponse(content="第二版"),
    ])
    _send(client, s["id"], "问题甲")
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/regenerate") as r:
        body = "".join(r.iter_text())
    assert "第二版" in body
    msgs = client.get(f"/api/cowork/sessions/{s['id']}/messages").json()
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant"]
    assert msgs[-1]["content"] == "第二版"


def test_regenerate_without_user_message_errors(client):
    s = _create_session(client)
    client.app.state.cowork_complete = FakeCoworkComplete()
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/regenerate") as r:
        body = "".join(r.iter_text())
    assert "没有可重新生成的提问" in body


def test_edit_rewrites_and_reruns(client):
    s = _create_session(client)
    client.app.state.cowork_complete = FakeCoworkComplete([
        LLMResponse(content="旧答案"),
        LLMResponse(content="标题"),
        LLMResponse(content="新答案"),
    ])
    _send(client, s["id"], "原问题")
    msgs = client.get(f"/api/cowork/sessions/{s['id']}/messages").json()
    user_mid = next(m["id"] for m in msgs if m["role"] == "user")
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages/{user_mid}/edit",
                       json={"content": "改后的问题"}) as r:
        body = "".join(r.iter_text())
    assert "新答案" in body
    msgs = client.get(f"/api/cowork/sessions/{s['id']}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "改后的问题"  # 就地改写
    assert msgs[1]["content"] == "新答案"


def test_edit_only_user_messages(client):
    s = _create_session(client)
    client.app.state.cowork_complete = FakeCoworkComplete([
        LLMResponse(content="答"), LLMResponse(content="题")])
    _send(client, s["id"], "问")
    msgs = client.get(f"/api/cowork/sessions/{s['id']}/messages").json()
    asst_mid = next(m["id"] for m in msgs if m["role"] == "assistant")
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages/{asst_mid}/edit",
                       json={"content": "x"}) as r:
        body = "".join(r.iter_text())
    assert "只能编辑用户消息" in body
