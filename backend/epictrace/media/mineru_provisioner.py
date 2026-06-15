from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Callable

# 注入点:默认用 subprocess.run;测试传假 uv_runner / models_runner,完全不碰真 uv/mineru/网络。
UvRunner = Callable[[list[str], int], subprocess.CompletedProcess]
ModelsRunner = Callable[[list[str], int], subprocess.CompletedProcess]
ProgressCb = Callable[[str], None]

# provision 阶段较长(建环境 + 装几 GB 依赖);给宽松超时。
_PROVISION_TIMEOUT = 3600
# 模型下载(几 GB)同样给宽松超时。
_DOWNLOAD_TIMEOUT = 3600

_log = logging.getLogger("epictrace")

# 模型下载(mineru-models-download → HuggingFace/ModelScope)进度行解析。
# 下载器把 tqdm 进度条写到 stderr(回车刷新同一行),形如:
#   `Downloading model.safetensors:  41%|███   | 1.2G/2.9G [00:30<00:42, ...]`
# 抓「百分比 + 文件名」转成简洁中文;无可识别信息 → None。
_DL_TQDM_RE = re.compile(r"^\s*(?:Downloading|Fetching)\b.*?(\d+)%")


def parse_download_progress_line(line: str) -> str | None:
    """把 mineru-models-download 的一行 stderr 解析成简洁进度串;无信息 → None。

    纯解析、不碰子进程,便于单测。仅抓下载百分比(tqdm),输出如 "下载模型 41%"。
    """
    text = line.strip()
    if not text:
        return None
    m = _DL_TQDM_RE.match(text)
    if m:
        return f"下载模型 {m.group(1)}%"
    return None


def stream_download_progress(lines: Iterable[str], progress_cb: ProgressCb) -> None:
    """逐行解析下载进度并回调(去重:同一进度串只报一次,避免 tqdm 刷屏)。

    抽出独立函数以便用假行序列单测,无需真下载器。
    """
    last: str | None = None
    for raw in lines:
        # tqdm 用 \r 在同一物理行刷新;按 \r 和 \n 双重切分,逐段解析。
        for piece in re.split(r"[\r\n]+", raw):
            msg = parse_download_progress_line(piece)
            if msg is not None and msg != last:
                progress_cb(msg)
                last = msg


def _default_uv_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, timeout=timeout, capture_output=True, text=True, check=False
    )


def _streaming_models_runner(
    cmd: list[str], timeout: int, progress_cb: ProgressCb
) -> subprocess.CompletedProcess:
    """跑 mineru-models-download 并逐行流式读取 stderr(下载 tqdm 进度走 stderr),边读边回调进度。

    与 mineru_runner._streaming_runner 同构:stdout=DEVNULL(下载结果落 HF/modelscope 缓存,
    stdout 不承载结果——否则 PIPE 缓冲在 stderr EOF 前被写满会死锁);timeout 由 threading.Timer
    真正强制(阻塞读 stderr 期间 communicate(timeout) 触发不了),到点 proc.kill() 让读循环 EOF 退出,
    再据 _timed_out 抛出。返回结构与 _default_uv_runner 一致(returncode/stdout/stderr)。
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1
    )
    err_lines: list[str] = []
    timed_out = threading.Event()

    def _on_timeout() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, _on_timeout)
    timer.start()

    def _consume() -> Iterable[str]:
        assert proc.stderr is not None
        for line in proc.stderr:
            err_lines.append(line)
            yield line

    try:
        stream_download_progress(_consume(), progress_cb)
    finally:
        timer.cancel()
    proc.wait()
    if timed_out.is_set():
        raise subprocess.TimeoutExpired(cmd, timeout)
    return subprocess.CompletedProcess(
        cmd, proc.returncode, stdout="", stderr="".join(err_lines)
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
        self._models_runner = models_runner
        self._failed = False
        # 失败阶段:install(装包)/ download(下模型)/ None(无失败)。
        # 区分二者使前端能把「重试」按钮指向正确动作;并在 cached 模型仍可用(state==ready)时
        # 也能暴露一次失败的重下(否则 sentinel 在 → state 显示 ready,失败被吞)。
        self._failed_stage: str | None = None
        # 并发守卫:_installing / _downloading 各自一个标志 + 共用条件变量(_cv,内含锁)。
        # 安装/下载中重复触发 no-op;state() 据标志暴露 "installing"/"downloading_models"。
        # _cv 让 ensure_models_ready 的等待者能阻塞到下载结束(notify_all 唤醒)。
        self._installing = False
        self._downloading = False
        self._cv = threading.Condition()
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
        # 已知取舍(接受):若用户在 APP 外手动删了缓存里的模型,sentinel 仍在 → state 显示
        # ready,失败只会在真正提取时暴露(而非 status)。这是有意为之,不在此处做磁盘探测。
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
    @property
    def failed_stage(self) -> str | None:
        return self._failed_stage

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
        任何失败(含 install 之前的 uv_bin() 报错)都置 failed + last_error + failed_stage=install,
        以便前端停止轮询;随后把原异常上抛(后台触发线程会捕获并记录)。"""
        # 单一守卫:仅一个线程进入安装;其余直接返回当前状态(no-op)。
        with self._cv:
            if self._installing:
                return self.state
            self._installing = True
            self._failed = False
            self.last_error = None
            self._failed_stage = None

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
            with self._cv:
                self._failed = True
                self._failed_stage = "install"
                self.last_error = str(e)[:500]
            _log.warning("MinerU provision failed: %s", e, exc_info=True)
            raise
        finally:
            # 无论成功/失败/被中断,安装线程结束都清 installing(state 据此回到 ready/failed)。
            with self._cv:
                self._installing = False
        return self.state

    def models_download_bin(self) -> str:
        return str(self._venv_dir / "bin" / "mineru-models-download")

    def ensure_models_ready(
        self,
        *,
        model_source: str = "modelscope",
        progress_cb: Callable[[str], None] | None = None,
        timeout: float = _DOWNLOAD_TIMEOUT + 60,
    ) -> None:
        """保证模型就绪后才返回——给提取前的「门」。

        - 已就绪(ready)→ 立即返回。
        - 正在下载(本线程或他线程触发)→ 阻塞在条件变量上直到下载结束:就绪则返回,
          失败/超时则抛(等待者也看到失败,绝不在未就绪时放行)。
        - 未下(installed_no_models)→ 本线程跑 download_models(其余并发 caller 走上一分支等待)。

        关键:download_models 自身对「下载中重复触发」是 no-op,所以并发的第二个 caller
        不能简单地再调它(会立即 no-op 返回、抢先去提取)。这里据 _downloading 标志区分:
        看到 downloading 就在 _cv 上等,而非调 download_models。
        竞态收尾:「都看到 not downloading」时两者都会调 download_models,只有一个真正跑、
        另一个 no-op 返回——no-op 那个不能直接返回(模型还没好),它会在下一圈循环里看到
        downloading 并落到等待分支。循环直到就绪或抛错,保证返回时一定 is_ready()。"""
        while True:
            if self.is_ready():
                return
            with self._cv:
                if self.is_ready():
                    return
                if self._downloading:
                    # 他线程(或刚才竞态里赢的线程)正在下载:阻塞到结束,据结果返回或抛
                    # (_wait_for_download_locked 仅在就绪时返回,失败/超时抛 RuntimeError)。
                    self._wait_for_download_locked(timeout)
                    return
            # 未在下载 → 本线程尝试驱动一次下载。竞态中 no-op 的线程会回到循环顶重判,
            # 在下一圈落到上面的 downloading 等待分支(不会抢先放行)。
            self.download_models(model_source=model_source, progress_cb=progress_cb)

    def _wait_for_download_locked(self, timeout: float) -> None:
        """调用方须持 _cv。等到当前下载结束(_downloading 落下),再据就绪/失败决定返回或抛。"""
        deadline = self._cv.wait_for(lambda: not self._downloading, timeout=timeout)
        if not deadline:
            raise RuntimeError("timed out waiting for MinerU model download")
        if self.is_ready():
            return
        raise RuntimeError(
            self.last_error or "MinerU model download failed"
        )

    def download_models(
        self,
        *,
        model_source: str = "modelscope",
        progress_cb: Callable[[str], None] | None = None,
    ) -> str:
        """下模型(跑 <venv>/bin/mineru-models-download -s <model_source> -m all)。返回结束时 state。

        hybrid 后端同时需要 pipeline 与 vlm 模型 → 传 -m all。成功后写 app-owned 就绪
        sentinel(见 models_ready_sentinel)并清失败标志。
        进度:未注入 models_runner(生产)且给了 progress_cb → 走 Popen 流式读 stderr 把
        下载进度逐条回调;注入了 models_runner(测试)或无 progress_cb → 一次性(不流式)。
        前提:已 provision(包就绪)。并发安全:下载中再调 no-op(返回当前 state),且唤醒
        在 ensure_models_ready 上等待的线程。
        任何失败置 failed + last_error + failed_stage=download 再上抛(后台触发线程捕获)。"""
        with self._cv:
            if self._downloading:
                return self.state
            self._downloading = True

        def emit(msg: str) -> None:
            if progress_cb is not None:
                progress_cb(msg)

        try:
            emit("正在下载模型(约数 GB,首次较久)…")
            # hybrid 需 pipeline + vlm 两套权重 → -m all。
            cmd = [self.models_download_bin(), "-s", model_source, "-m", "all"]
            self._run_models_or_fail(cmd, progress_cb)
            # 仅在子进程成功后写 app-owned 就绪 sentinel(权威就绪标志),并清失败标志。
            self.models_ready_sentinel().write_text("ready\n")
            with self._cv:
                self._failed = False
                self._failed_stage = None
                self.last_error = None
            emit("模型下载完成")
        except Exception as e:  # noqa: BLE001 — 任何失败都落到 failed + last_error
            with self._cv:
                # 注意:不删 sentinel——若曾成功下过,cached 模型仍可用(state 仍 ready);
                # 失败经 failed_stage + last_error 暴露,UI 据此显示重下失败。
                self._failed = True
                self._failed_stage = "download"
                self.last_error = str(e)[:500]
            _log.warning("MinerU model download failed: %s", e, exc_info=True)
            raise
        finally:
            # 清 downloading 并唤醒所有在 ensure_models_ready 上等待的线程(就绪或失败都唤醒)。
            with self._cv:
                self._downloading = False
                self._cv.notify_all()
        return self.state

    def _run_models_or_fail(
        self, cmd: list[str], progress_cb: Callable[[str], None] | None
    ) -> None:
        # 注入了 models_runner(测试)→ 一律用它(不流式);否则有 progress_cb 走流式 Popen,
        # 无则一次性 subprocess.run(默认行为)。
        if self._models_runner is not None:
            run: ModelsRunner = self._models_runner
        elif progress_cb is not None:
            run = lambda c, t: _streaming_models_runner(c, t, progress_cb)  # noqa: E731
        else:
            run = _default_uv_runner
        try:
            proc = run(cmd, _DOWNLOAD_TIMEOUT)
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
