"""ask_user 工具(需求 3):agent 循环中主动向用户提问。

复用审批通道(ApprovalManager,kind="question"):挂起循环 → SSE 推 approval_request →
前端弹窗带文本输入 → 回答作为工具结果返回。permission=allow——它本身就是询问机制,
再对它做权限闸门没有意义。
"""

from __future__ import annotations

import json
from collections.abc import Callable

from epictrace.cowork.approvals import ApprovalManager
from epictrace.cowork.permissions import APPROVAL_TIMEOUT_SEC
from epictrace.cowork.tools.registry import ToolDef


def build_ask_user_tool(
    approvals: ApprovalManager,
    *,
    session_id: int,
    on_request: Callable[[dict], None] | None = None,
    on_resolved: Callable[[str, str], None] | None = None,
) -> ToolDef:
    """on_request/on_resolved:SSE 事件回调(codex review R2:不发事件的话,活跃流里的
    前端看不到弹窗,提问会挂到超时)。dispatch_child 无 SSE,靠前端轮询 approvals。"""

    def ask_user(question: str) -> str:
        if not question.strip():
            return "Error: question 不能为空"
        req = approvals.request(
            session_id=session_id, tool="ask_user", args="",
            allow_session_option=False, kind="question", prompt=question.strip())
        if on_request is not None:
            on_request(req)
        answer = approvals.wait(req["approval_id"], APPROVAL_TIMEOUT_SEC)
        if on_resolved is not None:
            on_resolved(req["approval_id"], answer or "deny")
        if answer is None:
            return "(用户未及时回答,按无答复继续;不要反复追问同一个问题)"
        return f"用户的回答:{answer}"

    return ToolDef(
        name="ask_user",
        description=(
            "向用户提问并等待回答(弹窗带文本输入)。只在缺少关键信息、无法继续时使用;"
            "能自己查到的不要问。每次最多问一件事,问题要具体。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "要问用户的具体问题"},
            },
            "required": ["question"],
        },
        handler=ask_user,
        permission="allow",
    )


def approval_event(req: dict) -> dict:
    """把审批请求渲染成 SSE 事件 dict(与 service.before_tool 同格式)。"""
    return {"event": "approval_request", "data": json.dumps(req, ensure_ascii=False)}


def resolved_event(approval_id: str, decision: str) -> dict:
    return {"event": "approval_resolved", "data": json.dumps(
        {"approval_id": approval_id, "decision": decision}, ensure_ascii=False)}
