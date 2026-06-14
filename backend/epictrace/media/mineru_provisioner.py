from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

# 注入点:默认用 subprocess.run;测试传假 uv_runner,完全不碰真 uv/网络。
UvRunner = Callable[[list[str], int], subprocess.CompletedProcess]

# provision 阶段较长(建环境 + 装几 GB 依赖);给宽松超时。
_PROVISION_TIMEOUT = 3600


def _default_uv_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, timeout=timeout, capture_output=True, text=True, check=False
    )


class MinerUProvisioner:
    """管 <data_dir>/.MinerU-venv:用 uv 建隔离环境并装 mineru[all]。

    状态机:not_installed -> installing -> ready / failed。
    uv 调用注入(uv_runner)便于测试;uv_bin 可覆盖(dev: PATH;DMG: 打包内置)。
    """

    def __init__(
        self,
        venv_dir: Path,
        *,
        uv_bin: str | None = None,
        uv_runner: UvRunner | None = None,
    ) -> None:
        self._venv_dir = Path(venv_dir)
        self._uv_bin = uv_bin
        self._uv_runner = uv_runner or _default_uv_runner
        self._failed = False

    # ---- 路径 ----
    def mineru_bin(self) -> str:
        return str(self._venv_dir / "bin" / "mineru")

    def uv_bin(self) -> str:
        if self._uv_bin:
            return self._uv_bin
        found = shutil.which("uv")
        if not found:
            raise RuntimeError("uv not found on PATH; cannot provision MinerU")
        return found

    # ---- 状态 ----
    def is_ready(self) -> bool:
        return Path(self.mineru_bin()).exists()

    @property
    def state(self) -> str:
        if self.is_ready():
            return "ready"
        if self._failed:
            return "failed"
        return "not_installed"

    # ---- provision ----
    def provision(self, progress_cb: Callable[[str], None] | None = None) -> None:
        def emit(msg: str) -> None:
            if progress_cb is not None:
                progress_cb(msg)

        self._failed = False
        uv = self.uv_bin()
        self._venv_dir.parent.mkdir(parents=True, exist_ok=True)

        emit("创建隔离环境…")
        venv_cmd = [uv, "venv", "--python", "3.11", str(self._venv_dir)]
        self._run_or_fail(venv_cmd)

        emit("安装 MinerU(首次较久)…")
        install_cmd = [
            uv, "pip", "install", "--python", str(self._venv_dir),
            "mineru[all]",
        ]
        self._run_or_fail(install_cmd)
        emit("安装完成")

    def _run_or_fail(self, cmd: list[str]) -> None:
        try:
            proc = self._uv_runner(cmd, _PROVISION_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError) as e:
            self._failed = True
            raise RuntimeError(f"uv command failed: {e}") from e
        if proc.returncode != 0:
            self._failed = True
            raise RuntimeError(
                f"uv exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
            )
