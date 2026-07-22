"""统一工具注册表(需求 3)。

所有工具经 ToolRegistry 注册;每个工具携带名称、描述、JSON Schema 参数定义、
权限要求与沙箱要求。registry 输出 OpenAI function calling 兼容 schema;
执行失败时把错误信息返回给 LLM,而不是抛异常中断 agent 循环。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("epictrace.cowork")

# 权限等级(需求 7):ask=每次确认;ask-session=每 session 首次确认;allow=预批准
TOOL_PERMISSIONS = ("ask", "ask-session", "allow")
# 沙箱要求(需求 5):never=不沙箱;optional=可沙箱;required=必须沙箱
TOOL_SANDBOX = ("never", "optional", "required")


@dataclass(frozen=True)
class ToolDef:
    name: str                              # 唯一标识,如 read_file / mcp__websearch__search
    description: str                       # 给 LLM 读的功能描述
    parameters: dict                       # JSON Schema(object)
    handler: Callable[..., str]            # 执行体;返回给 LLM 的文本结果
    permission: str = "ask"
    sandbox: str = "never"
    always_allow_suppressed: bool = False  # True=禁止「总是允许」(删除/派发类工具)
    wants_ctx: bool = False                # True=handler 首参收执行上下文 dict(如 chunk 池)

    def __post_init__(self) -> None:
        if self.permission not in TOOL_PERMISSIONS:
            raise ValueError(f"invalid tool permission: {self.permission}")
        if self.sandbox not in TOOL_SANDBOX:
            raise ValueError(f"invalid tool sandbox: {self.sandbox}")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def list(self, names: list[str] | None = None) -> list[ToolDef]:
        """按注册顺序列出工具;names 给定时按白名单过滤(子 agent 用)。"""
        if names is None:
            return list(self._tools.values())
        return [self._tools[n] for n in names if n in self._tools]

    def openai_schemas(self, names: list[str] | None = None) -> list[dict]:
        """OpenAI function calling 兼容的工具 schema 列表。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters or {"type": "object", "properties": {}},
                },
            }
            for t in self.list(names)
        ]

    def execute(self, name: str, arguments_json: str, ctx: dict | None = None) -> str:
        """执行工具并把结果文本返回给 LLM。任何失败都转成错误文本,不抛异常。
        ctx:wants_ctx 工具的执行上下文(如 {"chunk_pool": [...]},由 AgentLoop 透传)。"""
        tool = self.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        try:
            args = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as e:
            return f"Error: invalid JSON arguments for {name}: {e}"
        if not isinstance(args, dict):
            return f"Error: arguments for {name} must be a JSON object"
        try:
            result = tool.handler(ctx, **args) if tool.wants_ctx else tool.handler(**args)
        except TypeError as e:
            return f"Error: bad arguments for {name}: {e}"
        except Exception as e:  # noqa: BLE001 — 工具失败是 LLM 的观察,不是循环的终止
            log.exception("tool %s failed", name)
            return f"Error: {type(e).__name__}: {e}"
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)
