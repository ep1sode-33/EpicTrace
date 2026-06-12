from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

_PROBE_SYS = "你能调用工具。请调用 echo 工具,参数 x 填 'ping'。"
_PROBE_USER = "调用 echo。"


@tool
def _echo(x: str) -> str:
    """Echo back x. (probe-only trivial tool)"""
    return x


def probe_tool_calling(chat_model) -> bool:
    """绑一个 trivial 工具,让模型调它,检查回包含结构合法的 tool_call。
    合法 → True;吐人话/坏结构/任何异常 → False(视为不支持,回退基础检索)。"""
    try:
        bound = chat_model.bind_tools([_echo])
        msg = bound.invoke([SystemMessage(content=_PROBE_SYS),
                            HumanMessage(content=_PROBE_USER)])
    except Exception:  # noqa: BLE001 — 任何探测故障一律视为不支持
        return False
    calls = getattr(msg, "tool_calls", None) or []
    for c in calls:
        # 结构合法:有名字 + args 是 dict。坏 JSON 时 langchain 会塞 invalid_tool_calls
        # 而非 tool_calls,故这里取不到 → False。
        if c.get("name") and isinstance(c.get("args"), dict):
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
