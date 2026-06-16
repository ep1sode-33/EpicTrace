from __future__ import annotations

import logging
import subprocess
import threading
import time

import numpy as np

_log = logging.getLogger("epictrace")

SAMPLE_RATE = 16000  # 全管线统一 16kHz mono float32(Whisper 期望)
_CHANNELS = 1
_PCM_DTYPE = np.float32

# 弱音 RMS 归一化默认目标 / 增益上限(可由 AsrConfig 覆盖)。
_DEFAULT_TARGET_DBFS = -20.0
_DEFAULT_MAX_GAIN_DB = 30.0
_SILENCE_RMS = 1e-4  # 近零能量阈:低于此不放大(否则把底噪轰起来)


def rms_normalize(pcm: np.ndarray, *, target_dbfs: float = _DEFAULT_TARGET_DBFS,
                  max_gain: float = _DEFAULT_MAX_GAIN_DB) -> np.ndarray:
    """把弱音抬到目标 dBFS(增益上限钳住,近静音不动)。喂模型前的弱音处理(spec §4)。

    纯函数(便于单测):RMS 低于近零阈值视为静音原样返回;否则按 target/当前 RMS 算增益,
    钳到 [0, max_gain] dB,放大后再 clip 到 [-1, 1] 防削顶溢出。
    """
    if pcm.size == 0:
        return pcm
    rms = float(np.sqrt(np.mean(np.square(pcm, dtype=np.float64))))
    if rms < _SILENCE_RMS:
        return pcm
    target_rms = 10.0 ** (target_dbfs / 20.0)
    max_gain_lin = 10.0 ** (max_gain / 20.0)
    # 把当前 RMS 抬到目标所需增益,钳到增益上限(防把底噪轰起来)。
    gain = min(target_rms / rms, max_gain_lin)
    out = pcm * gain
    # 放大后 clip 到 [-1, 1] 防削顶溢出(归一化目标低于 0dBFS,正常不触顶)。
    return np.clip(out, -1.0, 1.0).astype(_PCM_DTYPE)


class RingBuffer:
    """线程安全的滚动 PCM 累积:采集回调 push,转写循环 read 全量 + 查 pending。

    流式靠 faster-whisper 的 clip_timestamps 在全量 buffer 内 seek(绝不手动切 buffer),
    所以 read() 非破坏性返回累积的全部样本;pending_seconds() = 已累积总时长(调用方据
    last_confirmed_end 计算「未确认」秒数)。为防无限增长,超 max_seconds 丢弃最旧的尾巴
    (调用方确认进度推过后那段已不再需要)。
    """

    def __init__(self, *, sample_rate: int = SAMPLE_RATE, max_seconds: float = 600.0) -> None:
        self._sr = sample_rate
        self._max_samples = int(max_seconds * sample_rate)
        self._buf = np.empty(0, dtype=_PCM_DTYPE)
        self._lock = threading.Lock()

    def push(self, frames: np.ndarray) -> None:
        with self._lock:
            self._buf = np.concatenate([self._buf, frames.astype(_PCM_DTYPE, copy=False)])
            if self._buf.size > self._max_samples:
                self._buf = self._buf[-self._max_samples:]

    def read(self) -> np.ndarray:
        with self._lock:
            return self._buf.copy()

    def pending_seconds(self) -> float:
        with self._lock:
            return self._buf.size / float(self._sr)

    def sample_count(self) -> int:
        with self._lock:
            return int(self._buf.size)


class _SourceBase:
    """音源公共接口:start/stop + read()(全量 16k mono float32)+ pending_seconds()。
    子类在 push 前做 RMS 归一化(可关)。真采集 = 真机手测。"""

    def __init__(self, *, rms_normalize_enabled: bool = True) -> None:
        self._rb = RingBuffer()
        self._rms = rms_normalize_enabled
        self._stop = threading.Event()

    def _emit(self, frames: np.ndarray) -> None:
        if self._rms:
            frames = rms_normalize(frames)
        self._rb.push(frames)

    def read(self) -> np.ndarray:
        return self._rb.read()

    def pending_seconds(self) -> float:
        return self._rb.pending_seconds()

    def stop(self) -> None:
        self._stop.set()


class MicSource(_SourceBase):
    """麦克风外录:sounddevice 16kHz mono 输入流 + watchdog(样本不增长判失败重启一次)。

    sounddevice 在源 start() 时懒导入(避免测试套件硬依赖 PortAudio)。真采集手测。
    """

    def __init__(self, *, device=None, rms_normalize_enabled: bool = True) -> None:
        super().__init__(rms_normalize_enabled=rms_normalize_enabled)
        self._device = device
        self._stream = None
        self._wd_thread: threading.Thread | None = None

    def start(self) -> None:
        import sounddevice as sd  # 懒导入:测试不碰 PortAudio

        def _cb(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                _log.debug("mic stream status: %s", status)
            # indata: (frames, channels) float32;取单声道一维。
            self._emit(np.asarray(indata[:, 0], dtype=_PCM_DTYPE))

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=_CHANNELS, dtype="float32",
            device=self._device, callback=_cb,
        )
        self._stream.start()
        self._wd_thread = threading.Thread(target=self._watchdog, daemon=True)
        self._wd_thread.start()

    def _watchdog(self) -> None:
        """采集已启动但样本数不增长 → 判失败,重启流一次;仍死则记错(mic「假成功」坑)。"""
        last = self._rb.sample_count()
        time.sleep(2.0)
        if self._stop.is_set():
            return
        if self._rb.sample_count() == last:
            _log.warning("mic watchdog: no samples in 2s, restarting input stream once")
            try:
                if self._stream is not None:
                    self._stream.stop()
                    self._stream.close()
                self.start()
            except Exception as e:  # noqa: BLE001
                _log.error("mic restart failed: %s", e)

    def stop(self) -> None:
        super().stop()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None


class SystemAudioSource(_SourceBase):
    """macOS 系统内录:Popen 原生 helper 二进制,从其 stdout 读裸 PCM(16k mono float32 le)。

    helper 自己用 Core Audio process tap → 重采样到 16k mono(见 shell/native helper,Task 12)。
    读 stdout 的线程在 start() 起;真采集 + 权限 = 真机手测。
    """

    def __init__(self, helper_bin: str, *, rms_normalize_enabled: bool = True) -> None:
        super().__init__(rms_normalize_enabled=rms_normalize_enabled)
        self._bin = helper_bin
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [self._bin], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        # 每次读约 0.1s 的 float32 PCM(16000 * 0.1 * 4 字节);凑成整 float 帧再 push。
        chunk_bytes = int(SAMPLE_RATE * 0.1) * 4
        while not self._stop.is_set():
            raw = self._proc.stdout.read(chunk_bytes)
            if not raw:
                break  # helper 退出 / EOF
            n = len(raw) - (len(raw) % 4)
            if n <= 0:
                continue
            frames = np.frombuffer(raw[:n], dtype="<f4").astype(_PCM_DTYPE)
            self._emit(frames)

    def stop(self) -> None:
        super().stop()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
