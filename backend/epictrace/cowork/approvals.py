"""工具调用审批与提问的挂起-恢复通道(需求 7 / ask_user 工具)。

agent 循环在后台线程中执行;遇到需确认的工具(或 ask_user 主动提问)时,
经 ApprovalManager 挂起,SSE 把 approval_request 推给前端,用户决策后经
POST /cowork/approvals/{id} 回来,Event 唤醒循环线程继续。
超时未决策按拒绝处理(见 permissions.APPROVAL_TIMEOUT_SEC)。

kind 两种:
- permission:工具权限确认,decision ∈ {once, session, deny}
- question:ask_user 的自由文本提问,decision 是用户的回答文本
"""

from __future__ import annotations

import secrets
import threading

DECISIONS = ("once", "session", "deny")
# question 回答的长度上限(防失控文本灌入上下文)
MAX_ANSWER_LEN = 2000


class ApprovalManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}

    def request(
        self,
        *,
        session_id: int,
        tool: str,
        args: str,
        allow_session_option: bool,
        kind: str = "permission",
        prompt: str = "",
    ) -> dict:
        """登记一个待审批请求,返回给前端的描述(不含内部 Event)。"""
        approval_id = secrets.token_hex(8)
        entry = {
            "event": threading.Event(),
            "decision": None,
            "session_id": session_id,
            "tool": tool,
            "args": args,
            "allow_session_option": allow_session_option,
            "kind": kind,
            "prompt": prompt,
        }
        with self._lock:
            self._pending[approval_id] = entry
        return {
            "approval_id": approval_id,
            "session_id": session_id,
            "tool": tool,
            "args": args,
            "allow_session_option": allow_session_option,
            "kind": kind,
            "prompt": prompt,
        }

    def wait(self, approval_id: str, timeout: float,
             cancel: threading.Event | None = None) -> str | None:
        """循环线程阻塞等决策;permission 返回 once/session/deny,question 返回回答文本。
        超时或不存在返回 None;cancel 置位时立即返回 None(调用方按取消处理)。"""
        with self._lock:
            p = self._pending.get(approval_id)
        if p is None:
            return None
        if cancel is None:
            p["event"].wait(timeout)
        else:
            # 0.2s 轮询:取消信号(用户停止)要及时唤醒,不能睡满整个审批超时
            remaining = timeout
            while remaining > 0 and not p["event"].wait(min(0.2, remaining)):
                if cancel.is_set():
                    break
                remaining -= 0.2
        with self._lock:
            self._pending.pop(approval_id, None)
        return p["decision"]

    def decide(self, approval_id: str, decision: str) -> bool:
        """前端回传决策;唤醒等待中的循环线程。approval_id 不存在返回 False。
        非法取值(permission 非 once/session/deny;question 空串/超长)→ ValueError。"""
        with self._lock:
            p = self._pending.get(approval_id)
            if p is None:
                # 未知 id + 非法 decision 仍然报错(与早期行为一致);合法 decision 仅返回 False
                if decision not in DECISIONS:
                    raise ValueError(f"invalid decision: {decision}")
                return False
            if p["kind"] == "question":
                if not decision.strip() or len(decision) > MAX_ANSWER_LEN:
                    raise ValueError("invalid answer: empty or too long")
            elif decision not in DECISIONS:
                raise ValueError(f"invalid decision: {decision}")
            p["decision"] = decision
            p["event"].set()
            return True

    def pending(self) -> list[dict]:
        """当前待审批列表(前端刷新/重连后恢复弹窗用)。已决策但等待方尚未取走的不算。"""
        with self._lock:
            return [
                {
                    "approval_id": aid,
                    "session_id": p["session_id"],
                    "tool": p["tool"],
                    "args": p["args"],
                    "allow_session_option": p["allow_session_option"],
                    "kind": p["kind"],
                    "prompt": p["prompt"],
                }
                for aid, p in self._pending.items()
                if p["decision"] is None
            ]
