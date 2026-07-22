"""AgentLoop 单测(需求 1):多轮工具调用 / max_turns / 错误回传 / 白名单 / 事件。"""

import pytest

from epictrace.cowork.llm_client import LLMResponse, ToolCall
from epictrace.cowork.loop import AgentLoop, AgentLoopError
from epictrace.cowork.tools.registry import ToolDef, ToolRegistry
from tests.fakes import FakeCoworkComplete


def _registry_with_echo():
    r = ToolRegistry()
    r.register(ToolDef(
        name="echo",
        description="回声",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=lambda text="": f"echo:{text}",
    ))
    return r


def test_single_turn_end_turn():
    loop = AgentLoop(
        complete_fn=FakeCoworkComplete([LLMResponse(content="最终答案")]),
        registry=_registry_with_echo(),
        system_prompt="sys",
    )
    out = loop.run([{"role": "user", "content": "hi"}])
    assert out.text == "最终答案"
    assert out.turns == 1
    assert out.messages[0] == {"role": "system", "content": "sys"}


def test_tool_use_then_end_turn():
    fake = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="echo", arguments='{"text":"你好"}')]),
        LLMResponse(content="工具说 echo:你好"),
    ])
    loop = AgentLoop(complete_fn=fake, registry=_registry_with_echo(), system_prompt="sys")
    out = loop.run([{"role": "user", "content": "hi"}])
    assert out.text == "工具说 echo:你好"
    assert out.turns == 2
    # 第二轮调用应看到:assistant 的 tool_calls 消息 + role:tool 结果消息
    second_call_msgs = fake.calls[1][0]
    assert second_call_msgs[-2]["role"] == "assistant"
    assert second_call_msgs[-2]["tool_calls"][0]["function"]["name"] == "echo"
    assert second_call_msgs[-1] == {"role": "tool", "tool_call_id": "c1", "content": "echo:你好"}
    # 每次 LLM 调用都携带工具 schema(需求 1.3)
    assert fake.calls[0][1][0]["function"]["name"] == "echo"


def test_tool_exception_returned_to_llm_not_crash():
    def boom(**_):
        raise RuntimeError("工具炸了")

    r = ToolRegistry()
    r.register(ToolDef(name="boom", description="d", parameters={}, handler=boom))
    fake = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="boom", arguments="{}")]),
        LLMResponse(content="收到错误了"),
    ])
    loop = AgentLoop(complete_fn=fake, registry=r, system_prompt="sys")
    out = loop.run([{"role": "user", "content": "hi"}])
    assert out.text == "收到错误了"
    tool_msg = fake.calls[1][0][-1]
    assert tool_msg["role"] == "tool" and "工具炸了" in tool_msg["content"]


def test_unknown_tool_error_message():
    fake = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="ghost", arguments="{}")]),
        LLMResponse(content="好"),
    ])
    loop = AgentLoop(complete_fn=fake, registry=_registry_with_echo(), system_prompt="sys")
    out = loop.run([{"role": "user", "content": "hi"}])
    assert "unknown tool" in fake.calls[1][0][-1]["content"]
    assert out.text == "好"


def test_max_turns_exceeded():
    fake = FakeCoworkComplete(
        default=LLMResponse(tool_calls=[ToolCall(id="c", name="echo", arguments="{}")]))
    loop = AgentLoop(complete_fn=fake, registry=_registry_with_echo(),
                     system_prompt="sys", max_turns=3)
    with pytest.raises(AgentLoopError, match="最大轮数"):
        loop.run([{"role": "user", "content": "hi"}])
    assert len(fake.calls) == 3


def test_llm_failure_wrapped():
    def broken(_messages, _tools):
        raise ConnectionError("网络不通")

    loop = AgentLoop(complete_fn=broken, registry=_registry_with_echo(), system_prompt="sys")
    with pytest.raises(AgentLoopError, match="网络不通"):
        loop.run([{"role": "user", "content": "hi"}])


def test_allowed_tools_whitelist():
    fake = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="echo", arguments='{"text":"x"}')]),
        LLMResponse(content="结束"),
    ])
    loop = AgentLoop(complete_fn=fake, registry=_registry_with_echo(),
                     system_prompt="sys", allowed_tools=["other_tool"])
    out = loop.run([{"role": "user", "content": "hi"}])
    tool_msg = fake.calls[1][0][-1]
    assert "not available" in tool_msg["content"]
    assert out.text == "结束"
    # 白名单也收窄了下发给 LLM 的 schema
    assert fake.calls[0][1] == []


def test_events_emitted():
    events = []
    fake = FakeCoworkComplete([
        LLMResponse(reasoning="先想想", tool_calls=[ToolCall(id="c1", name="echo", arguments="{}")]),
        LLMResponse(content="完"),
    ])
    loop = AgentLoop(complete_fn=fake, registry=_registry_with_echo(),
                     system_prompt="sys", on_event=events.append)
    loop.run([{"role": "user", "content": "hi"}])
    kinds = [e["event"] for e in events]
    assert "thinking" in kinds
    assert kinds.count("tool_step") == 2  # started + done


def test_new_messages_only_contains_this_run():
    fake = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="echo", arguments="{}")]),
        LLMResponse(content="完"),
    ])
    loop = AgentLoop(complete_fn=fake, registry=_registry_with_echo(), system_prompt="sys")
    out = loop.run([{"role": "user", "content": "hi"}])
    assert [m["role"] for m in out.new_messages] == ["assistant", "tool", "assistant"]
