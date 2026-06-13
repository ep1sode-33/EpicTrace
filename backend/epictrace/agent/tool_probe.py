from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

_PROBE_TOOL_NAME = "epictrace_probe_echo"
_PROBE_SYS = (
    f"你能调用工具。请调用 {_PROBE_TOOL_NAME} 工具,参数 x 填 'ping'。")
_PROBE_USER = f"调用 {_PROBE_TOOL_NAME}。"


@tool(_PROBE_TOOL_NAME)
def _probe_echo(x: str) -> str:
    """Echo back x. (probe-only trivial tool)"""
    return x


def probe_tool_calling(chat_model) -> bool:
    """绑一个 trivial 工具,让模型调它,检查回包含结构合法的 tool_call。
    合法 → True;吐人话/坏结构/调错工具/带 invalid_tool_calls/任何异常 → False。"""
    try:
        bound = chat_model.bind_tools([_probe_echo])
        msg = bound.invoke([SystemMessage(content=_PROBE_SYS),
                            HumanMessage(content=_PROBE_USER)])
    except Exception:  # noqa: BLE001 — 任何探测故障一律视为不支持
        return False
    # 坏 JSON 时 langchain 把残件塞进 invalid_tool_calls;有任何这种残件 → 不算支持。
    if getattr(msg, "invalid_tool_calls", None):
        return False
    calls = getattr(msg, "tool_calls", None) or []
    for c in calls:
        # 结构合法:名字必须正好是探测工具名(防"瞎调别的名字"误判) + args 是 dict。
        if c.get("name") == _PROBE_TOOL_NAME and isinstance(c.get("args"), dict):
            return True
    return False


def _cache_key(profile: dict) -> tuple:
    return (profile.get("id"), profile.get("base_url"), profile.get("model"))


def cached_supports_tools(app_state, profile: dict, chat_model_factory) -> bool:
    """进程内缓存探测结果(键=profile id+base_url+model),存 app_state._tool_support。
    首次未命中 → 用 chat_model_factory(profile) 造模型探一次并缓存;重启重探。"""
    cache = getattr(app_state, "_tool_support", None)
    if cache is None:
        cache = {}
        app_state._tool_support = cache
    key = _cache_key(profile)
    if key in cache:
        return cache[key]
    verdict = probe_tool_calling(chat_model_factory(profile))
    cache[key] = verdict
    return verdict
