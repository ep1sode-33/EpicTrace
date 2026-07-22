"""沙箱执行(需求 5)。

高风险命令(bash / Python 脚本)在隔离环境中运行:
- **文件系统隔离**:每次执行在新建的临时目录(cwd + TMPDIR + HOME 全指向它),
  结束后整体删除;seatbelt 策略禁止写临时目录之外的路径
- **隔离机制**:macOS 原生 `sandbox-exec`(seatbelt)——deny default,只放行
  读全系统(解释器/库需要)、写临时目录与 /dev/null、进程创建;网络按档位放行
- **资源限制**:CPU 秒(ulimit -t,SIGXCPU)+ wall-clock 超时 kill;
  内存经 ulimit -v 设置,但 macOS 内核不强制 RLIMIT_AS——故内存上限在 macOS 是
  尽力而为(配置保留,Linux 上生效),这是已知平台限制
- **网络档位**:none(默认,seatbelt deny network*,含 localhost)/ unrestricted
- 执行结果(stdout / stderr / exit code / 是否超时)回传给 agent

不需要 VM 级隔离(需求 5 明确);sandbox-exec 不可用时(非 macOS)降级为
「临时目录 + 超时」并记 warning——工具仍可用,但不再保证文件系统隔离。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("epictrace.cowork")

# stdout/stderr 各端回传上限(超出截断,防巨型输出灌爆上下文)
OUTPUT_LIMIT = 10_000

_SANDBOX_EXEC = shutil.which("sandbox-exec")

# gRPC 日志行格式("I0721 02:02:21.650729 14944848 ev_poll_posix.cc:593] ...")。
# 主进程激活 Milvus 后,fork 子进程时 gRPC atfork handler 会把 poll 噪声写进子进程 stderr;
# GRPC_ENABLE_FORK_SUPPORT 只在 gRPC 初始化时读取(父进程早已初始化),env 补丁拦不住,
# 只能在回传前过滤。模式锚定 gRPC 日志前缀,不误伤正常输出。
_GRPC_LOG_LINE = re.compile(r"^[IWEF]\d{4} \d{2}:\d{2}:\d{2}\.\d+ +\d+ [\w.]+\.cc:\d+\].*$")


def _strip_grpc_noise(text: str) -> str:
    if not text:
        return text
    lines = [ln for ln in text.splitlines() if not _GRPC_LOG_LINE.match(ln)]
    return "\n".join(lines) + ("\n" if text.endswith("\n") and lines else "")


@dataclass(frozen=True)
class SandboxConfig:
    memory_mb: int = 512
    cpu_sec: int = 60
    network: str = "none"           # none | unrestricted
    wall_timeout_sec: float = 90.0  # wall-clock 兜底(应 > cpu_sec)


@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_sec: float

    def as_tool_text(self) -> str:
        """渲染为给 LLM 的工具结果文本。"""
        parts = []
        if self.timed_out:
            parts.append(f"[超时终止,已运行 {self.duration_sec:.0f}s]")
        parts.append(f"exit code: {self.exit_code}")
        parts.append(f"--- stdout ---\n{self.stdout or '(空)'}")
        parts.append(f"--- stderr ---\n{self.stderr or '(空)'}")
        return "\n".join(parts)


def _seatbelt_profile(workdir: Path, *, network: str) -> str:
    net = "(allow network*)" if network == "unrestricted" else "(deny network*)"
    return f"""(version 1)
(deny default)
(allow process-exec process-fork signal)
(allow mach-lookup)
(allow sysctl-read)
(allow file-read*)
(allow file-write* (subpath "{workdir}") (literal "/dev/null"))
{net}
"""


def run_sandboxed(
    command: str,
    *,
    config: SandboxConfig | None = None,
    setup: Callable[[Path], None] | None = None,
) -> SandboxResult:
    """在沙箱中经 bash 执行 command,返回结果。setup 可在执行前往临时目录放文件。"""
    cfg = config or SandboxConfig()
    workdir = Path(tempfile.mkdtemp(prefix="epictrace-sandbox-"))
    started = time.monotonic()
    try:
        if setup is not None:
            setup(workdir)
        # ulimit:CPU 秒硬限制(-t);内存 -v 在 macOS 不强制(见模块 docstring),Linux 生效
        wrapped = f"ulimit -t {cfg.cpu_sec}; ulimit -v {cfg.memory_mb * 1024} 2>/dev/null; {command}"
        argv = ["/bin/bash", "-c", wrapped]
        if _SANDBOX_EXEC is not None:
            # seatbelt 按解析后的真实路径判定(/var → /private/var 符号链接)
            argv = [_SANDBOX_EXEC, "-p",
                    _seatbelt_profile(workdir.resolve(), network=cfg.network), *argv]
        else:
            log.warning("sandbox-exec 不可用,降级为临时目录+超时(无文件系统/网络隔离)")
        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(workdir),
            "TMPDIR": str(workdir),
            "LANG": "en_US.UTF-8",
            # 主进程若已激活 Milvus(gRPC),fork 出的子进程会把 gRPC poll 噪声写进 stderr
            # ("FD from fork parent still in poll list")。子进程 exec 后根本不用 gRPC,
            # 关掉 fork 支持 + 压日志级别,保住 stderr 的干净(见 macos-embedding-milvus-fork-order)。
            "GRPC_ENABLE_FORK_SUPPORT": "0",
            "GRPC_VERBOSITY": "ERROR",
        }
        try:
            proc = subprocess.run(
                argv, cwd=workdir, env=env, capture_output=True, text=True,
                timeout=cfg.wall_timeout_sec,
            )
            return SandboxResult(
                stdout=proc.stdout[-OUTPUT_LIMIT:],
                stderr=_strip_grpc_noise(proc.stderr)[-OUTPUT_LIMIT:],
                exit_code=proc.returncode,
                timed_out=False,
                duration_sec=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired as e:
            return SandboxResult(
                stdout=(e.stdout or "")[-OUTPUT_LIMIT:] if isinstance(e.stdout, str) else "",
                stderr=(e.stderr or "")[-OUTPUT_LIMIT:] if isinstance(e.stderr, str) else "",
                exit_code=-1,
                timed_out=True,
                duration_sec=time.monotonic() - started,
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
