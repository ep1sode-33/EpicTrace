"""壳搬进包后的两条底线:可导入(无仓库布局假设)、packaged 模式绝不找 swiftc。"""
import pytest

webview = pytest.importorskip("webview")  # 无 GUI 依赖的环境跳过整文件


def test_shell_module_importable():
    import epictrace.shell  # noqa: F401
    from epictrace.shell import main  # noqa: F401


def test_ensure_helper_skips_swiftc_in_packaged_mode(monkeypatch, tmp_path):
    import epictrace.shell as shell

    monkeypatch.setenv("EPICTRACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EPICTRACE_PACKAGED", "1")

    def _boom(_name):
        raise AssertionError("packaged 模式不得调用 shutil.which 找 swiftc")

    monkeypatch.setattr("shutil.which", _boom)
    shell._ensure_sysaudio_helper()  # 不抛 = 在 packaged 分支提前返回
    assert not (tmp_path / "bin" / "epictrace-sysaudio").exists()


def test_ensure_helper_degrades_without_swiftc(monkeypatch, tmp_path):
    import epictrace.shell as shell

    monkeypatch.setenv("EPICTRACE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("EPICTRACE_PACKAGED", raising=False)
    monkeypatch.setattr("shutil.which", lambda _n: None)
    shell._ensure_sysaudio_helper()  # dev 无 swiftc:打日志降级,不抛
    assert not (tmp_path / "bin" / "epictrace-sysaudio").exists()
