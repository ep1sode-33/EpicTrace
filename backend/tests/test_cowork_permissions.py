"""PermissionEngine 单测(需求 7):四级权限 / admin 策略 / 通配符最严格优先 / 抑制列表。"""

import json

import pytest

from epictrace.config import AppConfig
from epictrace.services.settings import SettingsService
from epictrace.cowork.permissions import ALLOW, ASK, DENY, PermissionEngine
from epictrace.cowork.tools.registry import ToolDef


def _tool(name="t", permission="ask"):
    return ToolDef(name=name, description="d", parameters={}, handler=lambda: "",
                   permission=permission)


def _engine(tmp_path, *, mode="ask", overrides=None, admin=None):
    config = AppConfig(data_dir=tmp_path)
    svc = SettingsService(config)
    if mode != "ask" or overrides:
        svc.set_permission_settings({"mode": mode, "tool_overrides": overrides or {}})
    if admin is not None:
        (tmp_path / "admin_policy.json").write_text(json.dumps(admin), encoding="utf-8")
    return PermissionEngine(svc, config)


def test_tool_default_allow(tmp_path):
    e = _engine(tmp_path)
    d = e.decide(_tool(permission="allow"), session_mode="ask", session_approved=set())
    assert d.verdict == ALLOW


def test_tool_default_ask(tmp_path):
    e = _engine(tmp_path)
    d = e.decide(_tool(), session_mode="ask", session_approved=set())
    assert d.verdict == ASK and d.ask_session is False


def test_ask_session_tool_remembers(tmp_path):
    e = _engine(tmp_path)
    t = _tool(permission="ask-session")
    d1 = e.decide(t, session_mode="ask", session_approved=set())
    assert d1.verdict == ASK and d1.ask_session is True
    d2 = e.decide(t, session_mode="ask", session_approved={"t"})
    assert d2.verdict == ALLOW


def test_skip_all_auto_allows(tmp_path):
    e = _engine(tmp_path, mode="skip_all")
    d = e.decide(_tool(), session_mode="skip_all", session_approved=set())
    assert d.verdict == ALLOW


def test_skip_all_respects_user_explicit_ask(tmp_path):
    e = _engine(tmp_path, mode="skip_all", overrides={"t": "ask"})
    d = e.decide(_tool(), session_mode="skip_all", session_approved=set())
    assert d.verdict == ASK


def test_admin_blocked_beats_everything(tmp_path):
    e = _engine(tmp_path, mode="skip_all", admin={"tool_policies": {"t": "blocked"}})
    d = e.decide(_tool(permission="allow"), session_mode="skip_all", session_approved={"t"})
    assert d.verdict == DENY
    assert "admin" in d.reason


def test_admin_ask_is_floor_over_user_allow(tmp_path):
    e = _engine(tmp_path, overrides={"t": "allow"}, admin={"tool_policies": {"t": "ask"}})
    d = e.decide(_tool(), session_mode="ask", session_approved=set())
    assert d.verdict == ASK


def test_admin_allow_preapproves_tool_default(tmp_path):
    e = _engine(tmp_path, admin={"tool_policies": {"t": "allow"}})
    d = e.decide(_tool(), session_mode="ask", session_approved=set())
    assert d.verdict == ALLOW


def test_wildcard_strictest_wins(tmp_path):
    e = _engine(tmp_path, admin={"tool_policies": {"delete_*": "ask", "*": "allow",
                                                   "delete_file": "blocked"}})
    d = e.decide(_tool("delete_file"), session_mode="ask", session_approved=set())
    assert d.verdict == DENY
    d2 = e.decide(_tool("delete_folder"), session_mode="ask", session_approved=set())
    assert d2.verdict == ASK
    d3 = e.decide(_tool("read_file"), session_mode="ask", session_approved=set())
    assert d3.verdict == ALLOW


def test_admin_instructions_loaded(tmp_path):
    e = _engine(tmp_path, admin={"instructions": "禁止外发"})
    assert e.admin_instructions == "禁止外发"


def test_corrupt_admin_policy_treated_as_empty(tmp_path):
    (tmp_path / "admin_policy.json").write_text("{oops", encoding="utf-8")
    e = _engine(tmp_path)
    d = e.decide(_tool(), session_mode="ask", session_approved=set())
    assert d.verdict == ASK


@pytest.mark.parametrize("name", ["start_task", "start_code_task", "delete_project",
                                  "delete_file", "delete_anything"])
def test_suppressed_list(tmp_path, name):
    assert PermissionEngine.is_suppressed(name) is True


@pytest.mark.parametrize("name", ["read_file", "search_hybrid", "list_projects"])
def test_not_suppressed(tmp_path, name):
    assert PermissionEngine.is_suppressed(name) is False
