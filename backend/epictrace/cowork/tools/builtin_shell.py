"""Shell 工具(需求 5):run_bash / run_python —— 全部经沙箱执行(sandbox=required)。

沙箱配置取自 settings(memory_mb/cpu_sec/network),调用时现读,改设置即时生效。
工具只起 macOS 沙箱;macOS 之外 sandbox-exec 缺失时降级(见 sandbox.py docstring)。
"""

from __future__ import annotations

import shlex
import sys
from collections.abc import Callable

from epictrace.cowork.sandbox import SandboxConfig, run_sandboxed
from epictrace.cowork.tools.registry import ToolDef


def build_shell_tools(get_sandbox_settings: Callable[[], dict]) -> list[ToolDef]:
    def _config() -> SandboxConfig:
        s = get_sandbox_settings()
        return SandboxConfig(
            memory_mb=int(s["memory_mb"]),
            cpu_sec=int(s["cpu_sec"]),
            network=str(s["network"]),
            # wall-clock 兜底:CPU 计时之外,IO/睡眠也算,给 1.5 倍 + 30s 余量
            wall_timeout_sec=float(s["cpu_sec"]) * 1.5 + 30,
        )

    def run_bash(command: str) -> str:
        return run_sandboxed(command, config=_config()).as_tool_text()

    def run_python(code: str) -> str:
        def setup(wd):
            (wd / "main.py").write_text(code, encoding="utf-8")

        return run_sandboxed(
            f"{shlex.quote(sys.executable)} main.py", config=_config(), setup=setup,
        ).as_tool_text()

    return [
        ToolDef(
            name="run_bash",
            description=(
                "在隔离沙箱中执行 bash 命令(临时工作目录,执行后即焚;默认断网;"
                "CPU/内存有限制)。用于文档处理脚本、格式转换等。沙箱内写不了"
                "工作目录之外的路径,不要把产出依赖在跨调用的文件上。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 bash 命令"},
                },
                "required": ["command"],
            },
            handler=run_bash,
            permission="ask",
            sandbox="required",
        ),
        ToolDef(
            name="run_python",
            description=(
                "在隔离沙箱中执行 Python 代码(与 run_bash 同沙箱,解释器为后端同款 "
                "Python)。代码写入沙箱内 main.py 执行;stdout/stderr/exit code 回传。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python 源代码"},
                },
                "required": ["code"],
            },
            handler=run_python,
            permission="ask",
            sandbox="required",
        ),
    ]
