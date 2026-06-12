from types import SimpleNamespace

from langchain_core.messages import AIMessage

from epictrace.agent.tool_probe import (
    _PROBE_TOOL_NAME,
    cached_supports_tools,
    probe_tool_calling,
)
from tests.fakes import FakeChatModel


def _tool_call_msg():
    return AIMessage(content="", tool_calls=[
        {"name": _PROBE_TOOL_NAME, "args": {"x": "hi"}, "id": "1", "type": "tool_call"}])


def test_probe_true_on_valid_tool_call():
    assert probe_tool_calling(FakeChatModel(script=[_tool_call_msg()])) is True


def test_probe_false_on_prose():
    assert probe_tool_calling(FakeChatModel(script=[AIMessage(content="just talking")])) is False


def test_probe_false_on_wrong_tool_name():
    # Model calls *some* tool but not the actual probe tool → reject (false positive guard).
    msg = AIMessage(content="", tool_calls=[
        {"name": "some_other_tool", "args": {"x": "hi"}, "id": "1", "type": "tool_call"}])
    assert probe_tool_calling(FakeChatModel(script=[msg])) is False


def test_probe_false_when_invalid_tool_calls_present():
    # Even a valid-looking tool_call is rejected if the message also carries malformed attempts.
    msg = AIMessage(
        content="",
        tool_calls=[{"name": _PROBE_TOOL_NAME, "args": {"x": "hi"}, "id": "1", "type": "tool_call"}],
        invalid_tool_calls=[{"name": _PROBE_TOOL_NAME, "args": "{bad json", "id": "2",
                             "error": "could not parse", "type": "invalid_tool_call"}])
    assert probe_tool_calling(FakeChatModel(script=[msg])) is False


def test_probe_false_on_exception():
    class Boom:
        def bind_tools(self, tools, **kw): return self
        def invoke(self, messages, **kw): raise RuntimeError("no tools")
    assert probe_tool_calling(Boom()) is False


def test_cache_hit_skips_second_probe():
    state = SimpleNamespace()
    profile = {"id": "p1", "base_url": "u", "model": "m"}
    built = []

    def factory(p):
        built.append(1)
        return FakeChatModel(script=[_tool_call_msg()])

    assert cached_supports_tools(state, profile, factory) is True
    assert cached_supports_tools(state, profile, factory) is True
    assert built == [1]  # second call served from cache


def test_cache_keyed_by_profile_identity():
    state = SimpleNamespace()
    a = {"id": "p1", "base_url": "u", "model": "m"}
    b = {"id": "p2", "base_url": "u2", "model": "m2"}
    cached_supports_tools(state, a, lambda p: FakeChatModel(script=[_tool_call_msg()]))
    # different profile → not the same cache slot → re-probes (prose → False)
    assert cached_supports_tools(state, b, lambda p: FakeChatModel(
        script=[AIMessage(content="prose")])) is False
