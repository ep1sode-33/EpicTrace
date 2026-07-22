"""按 session 类型组装 system prompt(需求 2)。

不同 session 类型使用不同的 section 组合;组装结果以 DEBUG 日志输出完整 prompt(验收 2)。
组合顺序对齐 Cowork 推断顺序:身份 → 环境 → 技能/工具 → 安全/权限 → 流程/派发 →
用户/组织指令 → 任务(最后)。
"""

from __future__ import annotations

import logging

from epictrace.cowork.prompts.sections import SECTIONS, SectionContext

log = logging.getLogger("epictrace.cowork")

_LAYOUTS: dict[str, list[str]] = {
    "agent": [
        "identity", "environment", "tools", "skills", "attachments", "safety", "permissions",
        "workflow", "dispatch", "user_instructions", "admin_instructions", "task",
    ],
    "dispatch_child": [
        "identity", "environment", "tools", "skills", "safety", "permissions",
        "workflow", "admin_instructions", "task",
    ],
    "scheduled": [
        "identity", "environment", "tools", "skills", "safety", "permissions",
        "workflow", "scheduled", "admin_instructions", "task",
    ],
    "chat": ["identity", "environment", "user_instructions", "admin_instructions", "task"],
    "radar": ["identity", "environment", "tools", "safety", "workflow", "task"],
}


def assemble_system_prompt(ctx: SectionContext) -> str:
    """按 ctx.session_type 选 section 组合并渲染完整 system prompt。"""
    names = _LAYOUTS.get(ctx.session_type, _LAYOUTS["agent"])
    parts: list[str] = []
    for name in names:
        text = SECTIONS[name](ctx)
        if text and text.strip():
            parts.append(text.strip())
    prompt = "\n\n".join(parts)
    # 验收 2:日志中可看到完整的组装后 system prompt,各 section 按预期组合
    log.debug(
        "assembled system prompt (type=%s, sections=%s):\n%s",
        ctx.session_type, ",".join(names), prompt,
    )
    return prompt
