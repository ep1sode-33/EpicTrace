"""epictrace.shell 的 reveal_in_finder 路径守卫:不存在的路径不触发 `open -R`,存在才触发。
monkeypatch subprocess.run 以免真的弹 Finder。"""
import pytest

pytest.importorskip("webview")


@pytest.fixture()
def api():
    from epictrace.shell import Api

    return Api()


def test_reveal_skips_nonexistent_path(api, monkeypatch):
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a))
    assert api.reveal_in_finder("/no/such/path/zzz") == {"ok": False, "reason": "not_found"}
    assert api.reveal_in_finder("") == {"ok": False, "reason": "not_found"}
    assert calls == []  # 脏路径绝不调用 open


def test_reveal_runs_open_for_existing_path(api, monkeypatch, tmp_path):
    f = tmp_path / "real.txt"
    f.write_text("x", encoding="utf-8")
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a[0]))
    assert api.reveal_in_finder(str(f)) == {"ok": True}
    assert calls == [["open", "-R", str(f)]]  # argv 列表形式,不经 shell
