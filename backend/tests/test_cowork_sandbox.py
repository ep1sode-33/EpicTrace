"""沙箱测试(需求 5):隔离/资源限制/网络档位/结果回传 + shell 工具 + 设置 API。

验收 5:沙箱内的破坏性命令(rm/touch 沙箱外路径)不影响主机。
"""

import sys

import pytest

from epictrace.cowork.sandbox import OUTPUT_LIMIT, SandboxConfig, run_sandboxed
from epictrace.cowork.tools.builtin_shell import build_shell_tools
from epictrace.cowork.tools.registry import ToolRegistry

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="seatbelt 隔离语义是 macOS 专有的")


def test_basic_stdout_stderr_exit_code():
    r = run_sandboxed("echo out; echo err >&2; exit 3")
    assert r.exit_code == 3
    assert r.stdout.strip() == "out"
    assert r.stderr.strip() == "err"
    assert r.timed_out is False


def test_python_runs():
    r = run_sandboxed(f"{sys.executable} -c \"print('py ok')\"")
    assert r.exit_code == 0 and "py ok" in r.stdout


def test_filesystem_isolation(tmp_path):
    """验收 5:沙箱内删除/写主机路径被拒,主机文件无恙。"""
    victim = tmp_path / "important.txt"
    victim.write_text("珍贵数据", encoding="utf-8")
    r = run_sandboxed(f"rm -rf {tmp_path} 2>&1; echo exit-$?")
    assert "not permitted" in (r.stdout + r.stderr).lower() or "exit-0" not in r.stdout
    assert victim.read_text(encoding="utf-8") == "珍贵数据"


def test_write_inside_workdir_allowed():
    r = run_sandboxed("echo data > $HOME/out.txt && cat $HOME/out.txt && pwd")
    assert r.exit_code == 0
    assert "data" in r.stdout
    assert "epictrace-sandbox-" in r.stdout  # cwd 是临时目录


def test_network_none_blocks_even_localhost():
    code = (
        "import socket; s = socket.socket(); s.settimeout(2); "
        "s.connect(('127.0.0.1', 80))"
    )
    r = run_sandboxed(f"{sys.executable} -c \"{code}\" 2>&1 | tail -1")
    assert "not permitted" in r.stdout.lower()


def test_wall_timeout_kills():
    r = run_sandboxed("sleep 30", config=SandboxConfig(wall_timeout_sec=2))
    assert r.timed_out is True
    assert r.duration_sec < 10


def test_cpu_limit_kills():
    code = "while True: pass"
    r = run_sandboxed(f'{sys.executable} -c "{code}"',
                      config=SandboxConfig(cpu_sec=5, wall_timeout_sec=20))
    assert r.timed_out is False  # SIGXCPU 先于 wall 超时
    assert r.exit_code != 0
    assert r.duration_sec < 20


def test_output_truncated():
    r = run_sandboxed(f"{sys.executable} -c \"print('x' * 99999)\"")
    assert len(r.stdout) <= OUTPUT_LIMIT


def test_setup_writes_files():
    from pathlib import Path

    def setup(wd: Path):
        (wd / "input.txt").write_text("预备内容", encoding="utf-8")

    r = run_sandboxed("cat input.txt", setup=setup)
    assert "预备内容" in r.stdout


# ---- shell 工具 ----

def _registry_with_shell():
    r = ToolRegistry()
    for t in build_shell_tools(lambda: {"memory_mb": 512, "cpu_sec": 30, "network": "none"}):
        r.register(t)
    return r


def test_run_bash_tool():
    r = _registry_with_shell()
    tool = r.get("run_bash")
    assert tool.sandbox == "required" and tool.permission == "ask"
    out = r.execute("run_bash", '{"command": "echo 你好"}')
    assert "你好" in out and "exit code: 0" in out


def test_run_python_tool():
    r = _registry_with_shell()
    out = r.execute("run_python", '{"code": "print(6 * 7)"}')
    assert "42" in out


def test_tool_error_is_text_not_exception():
    r = _registry_with_shell()
    out = r.execute("run_bash", '{"command": "exit 1"}')
    assert "exit code: 1" in out  # 失败回传文本,不中断 agent 循环


# ---- 设置 API ----

def test_sandbox_settings_api(client):
    r = client.get("/api/settings/sandbox")
    assert r.json() == {"memory_mb": 512, "cpu_sec": 60, "network": "none"}

    r = client.put("/api/settings/sandbox", json={"cpu_sec": 30, "network": "unrestricted"})
    assert r.status_code == 200
    assert r.json()["cpu_sec"] == 30
    assert r.json()["network"] == "unrestricted"
    assert r.json()["memory_mb"] == 512  # 未带的键保留

    assert client.put("/api/settings/sandbox", json={"cpu_sec": 1}).status_code == 400
    assert client.put("/api/settings/sandbox", json={"network": "yolo"}).status_code == 400


def test_shell_tools_listed(client):
    names = {t["name"] for t in client.get("/api/cowork/tools").json()}
    assert {"run_bash", "run_python"} <= names
