import json
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.services.settings import SettingsService


def _svc(tmp_path: Path) -> SettingsService:
    return SettingsService(AppConfig(data_dir=tmp_path))


def test_create_first_profile_becomes_active(tmp_path: Path):
    svc = _svc(tmp_path)
    assert svc.is_configured() is False
    pid = svc.create_profile(name="A", base_url="http://x", api_key="k", model="m")
    assert svc.is_configured() is True
    active = svc.get_active_profile()
    assert active is not None and active["id"] == pid


def test_second_profile_does_not_steal_active(tmp_path: Path):
    svc = _svc(tmp_path)
    first = svc.create_profile(name="A", base_url="http://a", api_key="k1", model="m1")
    svc.create_profile(name="B", base_url="http://b", api_key="k2", model="m2")
    assert svc.public_view()["active_profile_id"] == first


def test_update_with_none_key_preserves_existing(tmp_path: Path):
    svc = _svc(tmp_path)
    pid = svc.create_profile(name="A", base_url="http://x", api_key="secret", model="m")
    # 仅改 model,api_key=None → 旧 key 必须保留
    svc.update_profile(pid, model="m2", api_key=None)
    active = svc.get_active_profile()
    assert active["api_key"] == "secret" and active["model"] == "m2"
    # 其余 None 也保留原值
    assert active["base_url"] == "http://x" and active["name"] == "A"
    # 显式传非 None(含空串)才替换
    svc.update_profile(pid, api_key="")
    assert svc.get_active_profile()["api_key"] == ""


def test_update_can_change_name_base_url_model(tmp_path: Path):
    svc = _svc(tmp_path)
    pid = svc.create_profile(name="A", base_url="http://x", api_key="k", model="m")
    svc.update_profile(pid, name="A2", base_url="http://y", model="m9")
    p = svc.get_active_profile()
    assert (p["name"], p["base_url"], p["model"]) == ("A2", "http://y", "m9")
    assert p["api_key"] == "k"  # 未传 → 保留


def test_public_view_includes_key(tmp_path: Path):
    # 本地单机:明文回传 api_key,允许前端查看/编辑/复制(不再遮罩)。
    svc = _svc(tmp_path)
    pid = svc.create_profile(name="A", base_url="http://x", api_key="topsecret", model="m")
    v = svc.public_view()
    assert v["configured"] is True
    assert v["active_profile_id"] == pid
    assert len(v["profiles"]) == 1
    prof = v["profiles"][0]
    assert prof["id"] == pid
    assert prof["name"] == "A" and prof["base_url"] == "http://x" and prof["model"] == "m"
    assert prof["api_key_set"] is True
    assert prof["api_key"] == "topsecret"
    assert "topsecret" in json.dumps(v, ensure_ascii=False)


def test_public_view_keyless_profile_marks_unset(tmp_path: Path):
    svc = _svc(tmp_path)
    svc.create_profile(name="local", base_url="http://localhost:11434/v1", api_key="", model="q")
    assert svc.public_view()["profiles"][0]["api_key_set"] is False
    # 无 key 也算「已配置」(本地端点)
    assert svc.is_configured() is True


def test_defaults_when_no_file(tmp_path: Path):
    v = _svc(tmp_path).public_view()
    assert v["configured"] is False
    assert v["active_profile_id"] is None
    assert v["profiles"] == []


def test_delete_active_reassigns_to_another(tmp_path: Path):
    svc = _svc(tmp_path)
    first = svc.create_profile(name="A", base_url="http://a", api_key="k1", model="m1")
    second = svc.create_profile(name="B", base_url="http://b", api_key="k2", model="m2")
    svc.delete_profile(first)  # 删的是活动 → 改指剩下的
    assert svc.public_view()["active_profile_id"] == second
    assert svc.is_configured() is True


def test_delete_last_clears_active(tmp_path: Path):
    svc = _svc(tmp_path)
    pid = svc.create_profile(name="A", base_url="http://a", api_key="k", model="m")
    svc.delete_profile(pid)
    v = svc.public_view()
    assert v["active_profile_id"] is None and v["profiles"] == []
    assert svc.is_configured() is False


def test_delete_nonactive_keeps_active(tmp_path: Path):
    svc = _svc(tmp_path)
    first = svc.create_profile(name="A", base_url="http://a", api_key="k1", model="m1")
    second = svc.create_profile(name="B", base_url="http://b", api_key="k2", model="m2")
    svc.delete_profile(second)  # 删非活动
    assert svc.public_view()["active_profile_id"] == first


def test_set_active_switches(tmp_path: Path):
    svc = _svc(tmp_path)
    first = svc.create_profile(name="A", base_url="http://a", api_key="k1", model="m1")
    second = svc.create_profile(name="B", base_url="http://b", api_key="k2", model="m2")
    assert svc.get_active_profile()["id"] == first
    svc.set_active(second)
    assert svc.get_active_profile()["id"] == second


def test_set_active_unknown_id_ignored(tmp_path: Path):
    svc = _svc(tmp_path)
    pid = svc.create_profile(name="A", base_url="http://a", api_key="k", model="m")
    svc.set_active("nope")
    assert svc.get_active_profile()["id"] == pid


def test_get_chat_llm_returns_active_values(tmp_path: Path):
    svc = _svc(tmp_path)
    first = svc.create_profile(name="A", base_url="http://a", api_key="k1", model="m1")
    second = svc.create_profile(name="B", base_url="http://b", api_key="k2", model="m2")
    chat = svc.get_chat_llm()
    assert (chat.base_url, chat.api_key, chat.model) == ("http://a", "k1", "m1")
    svc.set_active(second)
    chat2 = svc.get_chat_llm()
    assert (chat2.base_url, chat2.api_key, chat2.model) == ("http://b", "k2", "m2")


def test_get_chat_llm_none_when_unconfigured(tmp_path: Path):
    assert _svc(tmp_path).get_chat_llm() is None


def test_old_shape_migration(tmp_path: Path):
    # 旧形状 {"chat_llm": {...}} → 迁移为单个名为「默认」的活动 Profile
    p = tmp_path / "settings.json"
    p.write_text(
        json.dumps(
            {"chat_llm": {"base_url": "http://old", "api_key": "oldkey", "model": "oldm"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    svc = _svc(tmp_path)
    v = svc.public_view()
    assert v["configured"] is True
    assert len(v["profiles"]) == 1
    prof = v["profiles"][0]
    assert prof["name"] == "默认"
    assert prof["base_url"] == "http://old" and prof["model"] == "oldm"
    assert prof["api_key_set"] is True
    assert v["active_profile_id"] == prof["id"]
    # 迁移后 get_chat_llm 能取出旧 key
    assert svc.get_chat_llm().api_key == "oldkey"
    # 回归:迁移立刻落盘、id 稳定 → mutate 能真正改到(否则每次 _load 生成新 id,全部 no-op)
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert "profiles" in on_disk and on_disk["profiles"][0]["id"] == prof["id"]
    assert svc.public_view()["profiles"][0]["id"] == prof["id"]
    svc.update_profile(prof["id"], name="改名了")
    assert svc.get_active_profile()["name"] == "改名了"


def test_unknown_or_corrupt_file_does_not_crash(tmp_path: Path):
    (tmp_path / "settings.json").write_text("not json{{{", encoding="utf-8")
    v = _svc(tmp_path).public_view()
    assert v["configured"] is False and v["profiles"] == []
