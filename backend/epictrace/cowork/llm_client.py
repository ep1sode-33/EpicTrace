"""Cowork 的 LLM 调用通道(需求 1)。

loop 不直接依赖 openai SDK,而是依赖一个 `complete_fn(messages, tools) -> LLMResponse`
可调用对象——测试注入脚本化假件,生产用 make_complete_fn 构造真件(OpenAI 兼容端点,
DeepSeek 等)。finish_reason 映射需求中的 stop_reason:tool_calls→tool_use,其余→end_turn。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger("epictrace.cowork")

# 需求 1 的 stop_reason 语义(OpenAI finish_reason 的映射)
STOP_TOOL_USE = "tool_use"
STOP_END_TURN = "end_turn"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON 字符串(OpenAI 原样)


@dataclass
class LLMResponse:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = STOP_END_TURN  # tool_use | end_turn


CompleteFn = Callable[[list[dict], list[dict]], LLMResponse]


def make_complete_fn(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 120.0,
) -> CompleteFn:
    """构造基于 OpenAI 兼容端点的 complete_fn。每次调用新建 client(轻量,线程安全)。"""
    from epictrace.llm.openai_compat import _normalize_base_url

    normalized = _normalize_base_url(base_url)  # 容忍用户粘入整段端点(含 /chat/completions)

    def complete(messages: list[dict], tools: list[dict]) -> LLMResponse:
        from openai import OpenAI

        client = OpenAI(base_url=normalized, api_key=api_key or "none", timeout=timeout)
        kwargs: dict = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            # codex review P2:部分 OpenAI 兼容端点不收 tools 参数(400 系报错)。
            # 降级一次无工具重试——回答可用(无工具),而不是整轮报废。
            if not tools:
                raise
            log.info("端点拒绝 tools 参数,降级为无工具调用重试: %s", e)
            resp = client.chat.completions.create(model=model, messages=messages)
        choice = resp.choices[0]
        msg = choice.message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "")
            for tc in (msg.tool_calls or [])
        ]
        # DeepSeek reasoner 系列把推理放在 reasoning_content
        reasoning = getattr(msg, "reasoning_content", None) or ""
        return LLMResponse(
            content=msg.content or "",
            reasoning=reasoning,
            tool_calls=tool_calls,
            stop_reason=STOP_TOOL_USE if tool_calls else STOP_END_TURN,
        )

    return complete
