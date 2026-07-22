"""权限模型(需求 7)。

四级权限:ask(每次确认)/ follow_a_plan(计划内自动)/ skip_all(全部自动,仅本地)。
admin 策略(~/.epictrace/admin_policy.json)优先级最高,按工具名通配符配置:
  {"tool_policies": {"delete_*": "ask", "run_*": "blocked"}, "instructions": "..."}
多个通配符命中时取最严格:blocked > ask > ask-session > allow。
用户级覆盖(settings.json 的 permissions.tool_overrides)同规则,但永远不能让
admin 更严格的决定变松(取两者较严格者)。

特殊工具(start_task / delete_file / delete_* 等数据删除类)禁止「总是允许」,
前端只给「仅此一次 / 本次任务」选项(ALWAYS_ALLOW_SUPPRESSED)。

注意:follow_a_plan 目前行为等同 ask(计划审批流待后续迭代);skip_all 会跳过
一切确认,但 admin 的 blocked/ask 仍然生效。
"""

from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass

from epictrace.config import AppConfig
from epictrace.services.settings import SettingsService
from epictrace.cowork.tools.registry import ToolDef

log = logging.getLogger("epictrace.cowork")

# 决策结果
ALLOW = "allow"
ASK = "ask"
DENY = "deny"

# admin/用户策略的严格度序(数值大=严格,冲突取大)
_STRICTNESS = {"blocked": 4, "ask": 3, "ask-session": 2, "allow": 1}

# 禁止「总是允许」的工具名(需求 7;对齐 Cowork 的抑制列表)
ALWAYS_ALLOW_SUPPRESSED = frozenset({
    "start_task", "start_code_task", "delete_project", "delete_file",
})

# 等待用户批准的最长时间,超时按拒绝处理
APPROVAL_TIMEOUT_SEC = 300.0


@dataclass(frozen=True)
class Decision:
    verdict: str            # allow | ask | deny
    reason: str = ""        # 决策来源(admin 策略/用户覆盖/工具默认/session 模式)
    ask_session: bool = False  # True=批准一次后可记住「本次 session 都允许」


class PermissionEngine:
    def __init__(self, settings: SettingsService, config: AppConfig) -> None:
        self._settings = settings
        self._admin_policies, self._admin_instructions = _load_admin_policy(config)

    @property
    def admin_instructions(self) -> str:
        return self._admin_instructions

    @staticmethod
    def is_suppressed(tool_name: str) -> bool:
        """该工具是否禁止「总是允许」(只能仅此一次/本次任务)。"""
        if tool_name in ALWAYS_ALLOW_SUPPRESSED:
            return True
        # 任何数据删除类工具一律抑制(需求 7)
        return tool_name.startswith("delete_")

    def decide(self, tool: ToolDef, *, session_mode: str,
               session_approved: set[str]) -> Decision:
        """对一次工具调用做权限决策。

        session_mode: session 的 permission_mode(ask|follow_a_plan|skip_all)
        session_approved: 本 session 已被用户记住「都允许」的工具名集(ask-session 记忆)
        """
        # 1) admin 策略(最高优先级)
        admin = _strictest_match(self._admin_policies, tool.name)
        if admin == "blocked":
            return Decision(DENY, reason="admin 策略禁止该工具")
        # 2) 用户覆盖
        user = _strictest_match(self._user_overrides(), tool.name)
        # 3) 基线:用户显式覆盖 > admin 预批准 > 工具默认
        if user is not None:
            base = user
        elif admin == "allow":
            base = "allow"
        else:
            base = tool.permission
        # admin 的 ask/ask-session 是地板:最终决定不松于 admin(用户 allow 也压不过)
        if admin in ("ask", "ask-session") and _STRICTNESS[base] < _STRICTNESS[admin]:
            base = admin
        # 4) session 模式调制(skip_all 自动放行;admin 的 ask/blocked 不受影响;
        #    用户显式要求确认的工具在 skip_all 下仍确认)
        if session_mode == "skip_all" and admin not in ("ask", "ask-session", "blocked"):
            if user != "ask":
                return Decision(ALLOW, reason="skip_all 模式自动执行")
        # 5) ask-session 记忆
        if base == "allow":
            return Decision(ALLOW, reason="预批准")
        # admin 的 ask/ask-session 是地板:已记住的 session 批准不能压过 admin 的收紧
        # (codex review R2:admin 后改严时,存量 session 记忆不能绕过)
        if tool.name in session_approved and admin not in ("ask", "ask-session"):
            return Decision(ALLOW, reason="本 session 已批准")
        if base == "ask-session":
            return Decision(ASK, reason="首次使用需确认", ask_session=True)
        return Decision(ASK, reason="需要用户确认", ask_session=False)

    def _user_overrides(self) -> dict:
        try:
            return self._settings.get_permission_settings()["tool_overrides"]
        except Exception:  # noqa: BLE001 — 设置损坏不致命,按无覆盖处理
            return {}


def _strictest_match(policies: dict, tool_name: str) -> str | None:
    """返回命中该工具名的最严格策略值;无命中返回 None。"""
    best: str | None = None
    for pattern, verdict in (policies or {}).items():
        if verdict not in _STRICTNESS:
            continue
        if fnmatch.fnmatchcase(tool_name, pattern):
            if best is None or _STRICTNESS[verdict] > _STRICTNESS[best]:
                best = verdict
    return best


def _load_admin_policy(config: AppConfig) -> tuple[dict, str]:
    """组织级策略文件:~/.epictrace/admin_policy.json(只读;损坏按无策略处理)。"""
    path = config.data_dir / "admin_policy.json"
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            policies = data.get("tool_policies")
            if not isinstance(policies, dict):
                policies = {}
            return policies, str(data.get("instructions", "") or "")
    except (json.JSONDecodeError, OSError):
        log.warning("admin_policy.json 损坏,按无策略处理")
    return {}, ""
