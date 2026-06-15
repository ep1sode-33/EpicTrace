from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

# 注入点:默认用 subprocess.run;测试传假 uv_runner / models_runner,完全不碰真 uv/mineru/网络。
UvRunner = Callable[[list[str], int], subprocess.CompletedProcess]
ModelsRunner = Callable[[list[str], int], subprocess.CompletedProcess]

# provision 阶段较长(建环境 + 装几 GB 依赖);给宽松超时。
_PROVISION_TIMEOUT = 3600
# 模型下载(几 GB)同样给宽松超时。
_DOWNLOAD_TIMEOUT = 3600

_log = logging.getLogger("epictrace")


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
        models_runner: ModelsRunner | None = None,
    ) -> None:
        self._venv_dir = Path(venv_dir)
        self._uv_bin = uv_bin
        self._uv_runner = uv_runner or _default_uv_runner
        self._models_runner = models_runner or _default_uv_runner
        self._failed = False
        # 并发守卫:_installing / _downloading 各自一个标志 + 共用锁。安装/下载中重复触发
        # no-op;state() 据标志暴露 "installing"/"downloading_models"(前端徽标依赖它)。
        self._installing = False
        self._downloading = False
        self._lock = threading.Lock()
        self.last_error: str | None = None

    # ---- 路径 ----
    def mineru_bin(self) -> str:
        return str(self._venv_dir / "bin" / "mineru")

    def models_ready_sentinel(self) -> Path:
        # App-owned 就绪 sentinel,落在 venv 根目录旁(<venv>/.models-ready)。
        # 为何不查 MinerU 自己的磁盘 marker:已安装版本的 mineru-models-download 把权重
        # 下进 HuggingFace/modelscope 缓存(~/.cache/huggingface),venv 内既没有 models/
        # 目录也没有 mineru.json,没有稳定、跨版本的磁盘 marker 可依赖。
        # mineru-models-download 是幂等的(HF 缓存已有时为快速 no-op),所以即便模型已存在,
        # 由 sentinel 门控的重触发也很廉价——我们在一次成功下载后自行写入它作为权威就绪标志。
        return self._venv_dir / ".models-ready"

    def _models_ready(self) -> bool:
        return self.models_ready_sentinel().is_file()

    def uv_bin(self) -> str:
        if self._uv_bin:
            return self._uv_bin
        found = shutil.which("uv")
        if not found:
            raise RuntimeError("uv not found on PATH; cannot provision MinerU")
        return found

    # ---- 状态 ----
    def is_ready(self) -> bool:
        # 必须是真实文件(目录占位不算就绪)+ 模型已下;保持廉价,不起子进程探测。
        return Path(self.mineru_bin()).is_file() and self._models_ready()

    @property
    def state(self) -> str:
        # installing/downloading 优先于其余:对应线程活跃期间一律暴露该过渡态(前端轮询/徽标据此)。
        if self._installing:
            return "installing"
        if self._downloading:
            return "downloading_models"
        if self.is_ready():
            return "ready"
        if self._failed:
            return "failed"
        if Path(self.mineru_bin()).is_file():
            return "installed_no_models"  # 包就绪、模型未下
        return "not_installed"

    # ---- provision ----
    def provision(self, progress_cb: Callable[[str], None] | None = None) -> str:
        """建隔离环境 + 装 mineru[all]。返回结束时的 state 字符串。

        并发安全:已在安装中则 no-op(返回当前 state,不开第二次安装)。
        任何失败(含 install 之前的 uv_bin() 报错)都置 failed + last_error,
        以便前端停止轮询;随后把原异常上抛(后台触发线程会捕获并记录)。"""
        # 单一守卫:仅一个线程进入安装;其余直接返回当前状态(no-op)。
        with self._lock:
            if self._installing:
                return self.state
            self._installing = True
            self._failed = False
            self.last_error = None

        def emit(msg: str) -> None:
            if progress_cb is not None:
                progress_cb(msg)

        try:
            uv = self.uv_bin()  # 可能在此就抛(uv 不在 PATH)——也要被下面捕获置 failed
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
        except Exception as e:  # noqa: BLE001 — 任何失败都落到 failed + last_error
            with self._lock:
                self._failed = True
                self.last_error = str(e)[:500]
            _log.warning("MinerU provision failed: %s", e, exc_info=True)
            raise
        finally:
            # 无论成功/失败/被中断,安装线程结束都清 installing(state 据此回到 ready/failed)。
            with self._lock:
                self._installing = False
        return self.state

    def models_download_bin(self) -> str:
        return str(self._venv_dir / "bin" / "mineru-models-download")

    def download_models(
        self,
        *,
        model_source: str = "modelscope",
        progress_cb: Callable[[str], None] | None = None,
    ) -> str:
        """下模型(跑 <venv>/bin/mineru-models-download -s <model_source> -m all)。返回结束时 state。

        hybrid 后端同时需要 pipeline 与 vlm 模型 → 传 -m all。成功后写 app-owned 就绪
        sentinel(见 models_ready_sentinel)。
        前提:已 provision(包就绪)。并发安全:下载中再调 no-op(返回当前 state)。
        任何失败置 failed + last_error 再上抛(后台触发线程捕获)。"""
        with self._lock:
            if self._downloading:
                return self.state
            self._downloading = True
            self._failed = False
            self.last_error = None

        def emit(msg: str) -> None:
            if progress_cb is not None:
                progress_cb(msg)

        try:
            emit("正在下载模型(约数 GB,首次较久)…")
            # hybrid 需 pipeline + vlm 两套权重 → -m all。
            cmd = [self.models_download_bin(), "-s", model_source, "-m", "all"]
            self._run_models_or_fail(cmd)
            # 仅在子进程成功后写 app-owned 就绪 sentinel(权威就绪标志)。
            self.models_ready_sentinel().write_text("ready\n")
            emit("模型下载完成")
        except Exception as e:  # noqa: BLE001 — 任何失败都落到 failed + last_error
            with self._lock:
                self._failed = True
                self.last_error = str(e)[:500]
            _log.warning("MinerU model download failed: %s", e, exc_info=True)
            raise
        finally:
            with self._lock:
                self._downloading = False
        return self.state

    def _run_models_or_fail(self, cmd: list[str]) -> None:
        try:
            proc = self._models_runner(cmd, _DOWNLOAD_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RuntimeError(f"model download failed: {e}") from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"mineru-models-download exited {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:500]}"
            )

    def _run_or_fail(self, cmd: list[str]) -> None:
        try:
            proc = self._uv_runner(cmd, _PROVISION_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RuntimeError(f"uv command failed: {e}") from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"uv exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
            )
