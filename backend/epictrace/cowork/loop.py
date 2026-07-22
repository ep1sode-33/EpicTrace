"""多轮 agent 循环(需求 1)。

`思考 → 工具调用 → 观察结果 → 继续思考`,直到 LLM 返回 end_turn 产出最终文本。
- 每次调用 LLM 携带当前 session 的工具 schema(OpenAI function calling 格式)
- stop_reason=tool_use → 执行工具,结果以 role:tool 消息追加,继续循环
- stop_reason=end_turn → 结束循环,返回文本
- 最大轮数可配置(默认 50),超过抛 AgentLoopError
- 单轮 wall-clock 超时保护(LLM 调用本身的超时由 complete_fn 的 client timeout 保证)
- 工具执行失败不中断循环(见 registry.execute)
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from epictrace.cowork.llm_client import CompleteFn, LLMResponse
from epictrace.cowork.tools.registry import ToolRegistry

log = logging.getLogger("epictrace.cowork")

DEFAULT_MAX_TURNS = 50
DEFAULT_TURN_TIMEOUT = 120.0  # 秒


class AgentLoopError(Exception):
    def __init__(self, message: str, *, partial: list[dict] | None = None) -> None:
        super().__init__(message)
        # 失败前已执行的 assistant/tool 消息(调用方应落库,否则副作用无据可查)
        self.partial = partial or []


@dataclass
class LoopResult:
    text: str
    messages: list[dict]        # 完整消息历史(含 system),供持久化
    turns: int
    new_messages: list[dict] = field(default_factory=list)  # 本轮新增的 assistant/tool 消息


# on_event 事件类型:thinking(推理流)/ tool_step(工具调用步骤)
EventFn = Callable[[dict], None]


class AgentLoop:
    def __init__(
        self,
        *,
        complete_fn: CompleteFn,
        registry: ToolRegistry,
        system_prompt: str,
        max_turns: int = DEFAULT_MAX_TURNS,
        turn_timeout: float = DEFAULT_TURN_TIMEOUT,
        allowed_tools: list[str] | None = None,   # 子 agent 工具白名单;None=全部
        before_tool: Callable[[dict], str | None] | None = None,  # 权限闸门:返回 None=放行,字符串=作为工具结果跳过执行
        on_event: EventFn | None = None,
        exec_ctx: dict | None = None,             # wants_ctx 工具的执行上下文(如 {"chunk_pool": [...]})
        should_stop: Callable[[], bool] | None = None,  # 外部取消信号(用户点停止)
    ) -> None:
        self._complete = complete_fn
        self._registry = registry
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._turn_timeout = turn_timeout
        self._allowed_tools = allowed_tools
        self._before_tool = before_tool
        self._on_event = on_event or (lambda e: None)
        self._exec_ctx = exec_ctx
        self._should_stop = should_stop or (lambda: False)

    def run(self, messages: list[dict]) -> LoopResult:
        """messages 为不含 system 的历史(OpenAI 格式);返回最终文本与完整历史。"""
        msgs: list[dict] = [{"role": "system", "content": self._system_prompt}, *messages]
        base_len = len(msgs)
        schemas = self._registry.openai_schemas(self._allowed_tools)

        for turn in range(1, self._max_turns + 1):
            if self._should_stop():
                raise AgentLoopError("已被用户停止", partial=msgs[base_len:])
            started = time.monotonic()
            try:
                resp = self._complete(msgs, schemas)
            except Exception as e:  # noqa: BLE001 — 网络/限流/鉴权统一报错终止本轮
                raise AgentLoopError(
                    f"LLM 调用失败(第 {turn} 轮): {type(e).__name__}: {e}",
                    partial=msgs[base_len:]) from e
            # 单轮超时只计 LLM 调用本身:工具执行(wait_task 600s / 审批 300s / 转写 1800s)
            # 有各自的超时上限,不算在 turn 预算内(codex review P1)。
            if time.monotonic() - started > self._turn_timeout:
                raise AgentLoopError(
                    f"单轮 LLM 调用超时(第 {turn} 轮,>{self._turn_timeout:.0f}s)",
                    partial=msgs[base_len:])

            if resp.reasoning:
                self._on_event({"event": "thinking", "data": resp.reasoning})

            if not resp.tool_calls:
                text = resp.content or ""
                msgs.append({"role": "assistant", "content": text})
                return LoopResult(text=text, messages=msgs, turns=turn,
                                  new_messages=msgs[base_len:])

            # stop_reason=tool_use:追加 assistant 的工具调用消息,逐个执行并回填结果
            msgs.append(_assistant_tool_message(resp))
            for tc in resp.tool_calls:
                if self._should_stop():
                    raise AgentLoopError("已被用户停止", partial=msgs[base_len:])
                if self._allowed_tools is not None and tc.name not in self._allowed_tools:
                    result = f"Error: tool '{tc.name}' is not available in this session"
                else:
                    denial: str | None = None
                    if self._before_tool is not None:
                        denial = self._before_tool(
                            {"id": tc.id, "name": tc.name, "arguments": tc.arguments})
                    if denial is not None:
                        result = denial
                    else:
                        self._on_event({"event": "tool_step", "data": json.dumps(
                            {"tool": tc.name, "args": _preview_args(tc.arguments), "status": "started"},
                            ensure_ascii=False)})
                        result = self._registry.execute(tc.name, tc.arguments, ctx=self._exec_ctx)
                self._on_event({"event": "tool_step", "data": json.dumps(
                    {"tool": tc.name, "status": "done", "preview": result[:200]},
                    ensure_ascii=False)})
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        raise AgentLoopError(f"超过最大轮数({self._max_turns}),任务未完成",
                             partial=msgs[base_len:])


def _assistant_tool_message(resp: LLMResponse) -> dict:
    return {
        "role": "assistant",
        "content": resp.content or None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in resp.tool_calls
        ],
    }


def _preview_args(arguments: str, limit: int = 120) -> str:
    text = " ".join((arguments or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
