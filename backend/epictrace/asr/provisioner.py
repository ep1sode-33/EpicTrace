from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

# 注入点:默认 runner 实例化 WhisperModel 触发下载;测试传假 runner,完全不碰真 faster-whisper/网络。
# 注意:faster_whisper 只在默认 runner *函数体内* 懒加载,使本模块在未装 faster-whisper 时也能导入。
ProgressCb = Callable[[str], None]
DownloadRunner = Callable[[str, Path, ProgressCb], None]

_log = logging.getLogger("epictrace")

# 默认 HuggingFace hub 缓存根。faster-whisper(WhisperModel(model, download_root=...))把权重落进
# 这里,仓库目录命名形如 `models--Systran--faster-whisper-<model>`;distil 系列经第三方镜像,
# 目录名形如 `models--*distil*<model>*`(owner 不固定),故两族 glob 都查。
_DEFAULT_HF_CACHE_DIR = Path.home() / ".cache" / "huggingface" / "hub"


def detect_asr_model(cache_dir: Path, model: str) -> bool:
    """检测 HF 缓存里是否已下该 faster-whisper 模型。

    纯文件系统探测,不起子进程;缓存根不存在 → False(不崩)。
    匹配 Systran 官方仓库目录(models--Systran--faster-whisper-<model>)或 distil 镜像
    (models--*distil*<model>*),任一命中即视为就绪。
    """
    if not cache_dir.is_dir():
        return False
    globs = (
        f"models--Systran--faster-whisper-{model}",
        f"models--*distil*{model}*",
    )
    try:
        for g in globs:
            for entry in cache_dir.glob(g):
                if entry.is_dir():
                    return True
    except OSError:
        return False
    return False


def _default_download_runner(model: str, cache_dir: Path, progress_cb: ProgressCb) -> None:
    """默认下载:实例化 WhisperModel 触发权重拉取到 cache_dir。

    懒加载 faster_whisper(只在此函数体内 import),使 provisioner 模块在未装该重依赖时仍可导入,
    测试也可注入假 runner 完全绕开真下载。
    """
    from faster_whisper import WhisperModel  # 懒加载:未装也不影响模块导入

    progress_cb("正在下载模型(首次较久)…")
    WhisperModel(model, download_root=str(cache_dir))
    progress_cb("模型下载完成")


class AsrProvisioner:
    """管 faster-whisper 模型下载/就绪检测(仿 MinerUProvisioner)。

    状态机:not_downloaded -> downloading -> ready / failed。
    下载 runner 注入(download_runner)便于测试;cache_dir 可覆盖(默认 HF hub 缓存)。
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        download_runner: DownloadRunner | None = None,
    ) -> None:
        self._cache_dir = Path(cache_dir) if cache_dir is not None else _DEFAULT_HF_CACHE_DIR
        self._runner = download_runner or _default_download_runner
        self._failed = False
        # 并发守卫:下载中重复触发 no-op;_cv 内含锁,state 据 _downloading 暴露过渡态。
        self._downloading = False
        self._cv = threading.Condition()
        self.last_error: str | None = None
        # 最近一次成功/尝试下载的模型,供无参 is_ready/state 检测就绪。
        self._last_model: str | None = None

    # ---- 路径 ----
    def cache_dir(self) -> Path:
        """模型就绪检测/下载的 HF hub 缓存根(默认 ~/.cache/huggingface/hub,可注入)。"""
        return self._cache_dir

    # ---- 状态 ----
    def is_ready(self, model: str) -> bool:
        # 廉价文件探测,不起子进程:HF 缓存里存在该 faster-whisper 模型目录即就绪。
        return detect_asr_model(self._cache_dir, model)

    @property
    def state(self) -> str:
        # downloading 优先:下载线程活跃期间一律暴露该过渡态(前端轮询/徽标据此)。
        if self._downloading:
            return "downloading"
        if self._last_model is not None and self.is_ready(self._last_model):
            return "ready"
        if self._failed:
            return "failed"
        return "not_downloaded"

    # ---- 下载 ----
    def download_model(
        self, model: str, *, progress_cb: ProgressCb | None = None
    ) -> str:
        """下指定 faster-whisper 模型(默认 runner 实例化 WhisperModel 触发)。返回结束时 state。

        并发安全:下载中再调 no-op(返回当前 state),且唤醒在等待的线程。
        任何失败置 failed + last_error 再上抛(后台触发线程捕获并记录)。
        """
        with self._cv:
            if self._downloading:
                return self.state
            self._downloading = True
            self._failed = False
            self.last_error = None
            self._last_model = model

        def emit(msg: str) -> None:
            if progress_cb is not None:
                progress_cb(msg)

        try:
            self._runner(model, self._cache_dir, emit)
        except Exception as e:  # noqa: BLE001 — 任何失败都落到 failed + last_error
            with self._cv:
                self._failed = True
                self.last_error = str(e)[:500]
            _log.warning("ASR model download failed: %s", e, exc_info=True)
            raise
        finally:
            # 无论成功/失败,下载线程结束都清 downloading 并唤醒等待者(state 据此回到 ready/failed)。
            with self._cv:
                self._downloading = False
                self._cv.notify_all()
        return self.state
