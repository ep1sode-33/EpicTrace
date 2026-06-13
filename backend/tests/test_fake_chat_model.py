from langchain_core.messages import AIMessage, HumanMessage

from tests.fakes import FakeChatModel


def test_scripted_ai_messages_in_order():
    m = FakeChatModel(script=[
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "1", "type": "tool_call"}]),
        AIMessage(content="done"),
    ])
    bound = m.bind_tools([])  # bind_tools returns a model that yields the same script
    first = bound.invoke([HumanMessage(content="hi")])
    assert first.tool_calls and first.tool_calls[0]["name"] == "t"
    second = bound.invoke([HumanMessage(content="hi")])
    assert second.content == "done" and not second.tool_calls


def test_runs_out_of_script_returns_plain_answer():
    m = FakeChatModel(script=[], default=AIMessage(content="fallthrough"))
    assert m.bind_tools([]).invoke([HumanMessage(content="x")]).content == "fallthrough"
