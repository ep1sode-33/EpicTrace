"""codex review R3 回归:turn 锁不残留消息 / local 强制 1024 / 签名文件 / 项目校验 / 软链守卫。"""

import json
import threading
import time

from epictrace.cowork.llm_client import LLMResponse
from tests.fakes import FakeCoworkComplete


def test_rejected_turn_leaves_no_message(client):
    """并发被拒的第二个请求:不在历史里留下 user 消息(R3-P1)。"""
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    gate = threading.Event()

    def slow(messages, tools):
        gate.wait(10)
        return LLMResponse(content="慢答")

    client.app.state.cowork_complete = slow

    def first():
        with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                           json={"content": "第一问"}) as r:
            "".join(r.iter_text())

    t = threading.Thread(target=first)
    t.start()
    time.sleep(0.5)
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "第二问"}) as r:
        body2 = "".join(r.iter_text())
    assert "正在运行中" in body2
    gate.set()
    t.join(timeout=15)
    msgs = client.get(f"/api/cowork/sessions/{s['id']}/messages").json()
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 1  # 被拒的第二问没落库
    assert user_msgs[0]["content"] == "第一问"


def test_local_provider_forces_1024(tmp_path):
    """remote 配过非 1024 维度后切回 local:读出的 dimensions 归一为 1024(R3-P1)。"""
    from epictrace.config import AppConfig
    from epictrace.services.settings import SettingsService

    svc = SettingsService(AppConfig(data_dir=tmp_path))
    svc.set_embedding_settings({"provider": "remote", "dimensions": 768,
                                "base_url": "https://x", "model": "m"})
    svc.set_embedding_settings({"provider": "local"})
    assert svc.get_embedding_settings()["dimensions"] == 1024


def test_sig_file_written_and_compared(client, tmp_path):
    """store 构造后写 .embedsig.json;签名不一致判定为 stale(R3-P1)。"""
    from epictrace.api.deps import _sig_file_matches, _write_sig_file

    p = str(tmp_path / "v.db")
    sig = ("remote", "https://a", "m1", 768)
    assert _sig_file_matches(p, sig)  # 无文件(旧库/新库)→ 一致
    _write_sig_file(p, sig)
    assert _sig_file_matches(p, sig)
    assert not _sig_file_matches(p, ("remote", "https://b", "m1", 768))  # 换端点 → stale
    assert not _sig_file_matches(p, ("local", "", "", 1024))


def test_create_session_invalid_project_404(client):
    r = client.post("/api/cowork/sessions", json={"type": "agent", "project_id": 9999})
    assert r.status_code == 404


def test_list_files_skips_dir_symlinks(tmp_path):
    """项目内指向外部的目录软链不展开(R3-P2)。"""
    from epictrace.config import AppConfig
    from epictrace.db import Database
    from epictrace.cowork.tools.builtin_fs import build_fs_tools
    from epictrace.cowork.tools.registry import ToolRegistry

    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    from epictrace.services.projects import ProjectService

    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / "正常.txt").write_text("x", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "秘密.txt").write_text("secret", encoding="utf-8")
    (proj_dir / "逃逸").symlink_to(outside, target_is_directory=True)
    proj = ProjectService(db).create(title="P", folder_path=str(proj_dir))

    registry = ToolRegistry()
    for t in build_fs_tools(db):
        registry.register(t)
    out = registry.execute("list_files", json.dumps({"project_id": proj.id}))
    assert "秘密.txt" not in out
    assert "软链,不展开" in out
    assert "正常.txt" in out
