from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

_log = logging.getLogger("epictrace")

# 后端识别的两个音频源(其余源如 note/clipboard/screenshot 不走 ASR 子进程)。
_AUDIO_SOURCES = ("mic", "system_audio")

# 注入点:默认用 subprocess.Popen 拉起 worker;测试传假 spawn 完全不起真子进程。
# 返回一个具 terminate()/poll() 的句柄(真件为 Popen)。
Spawn = Callable[[list[str]], object]


def _default_spawn(argv: list[str]) -> subprocess.Popen:
    # 用当前解释器替换 "python",保证子进程跑在同一 venv(否则可能落到系统 python)。
    cmd = [sys.executable] + argv[1:] if argv and argv[0] == "python" else list(argv)
    return subprocess.Popen(cmd)


@dataclass
class _Entry:
    proc: object
    sources: list[str]
    staging_dir: str
    model: str


class AsrSupervisor:
    """后端侧:按 session 拉起/停止 ASR worker 子进程(faster-whisper 隔离在子进程,
    避开 embedder/Milvus 的 macOS fork 段错误)。只有选了 mic/system_audio 才起。

    spawn 注入便于测试(默认 subprocess.Popen)。pause/resume 用最简的「停 + 重启」实现:
    暂停即 terminate worker,恢复即按原参数重新 spawn(原始音频 wav 以追加方式续写)。
    """

    def __init__(self, *, spawn: Spawn | None = None) -> None:
        self._spawn = spawn or _default_spawn
        self._procs: dict[int, _Entry] = {}
        # pause/resume 暂存:session_id -> (sources, staging_dir, model)
        self._paused: dict[int, tuple[list[str], str, str]] = {}

    @staticmethod
    def _audio_sources(sources: list[str]) -> list[str]:
        # 保持调用方给定的顺序,仅保留识别的音频源。
        return [s for s in sources if s in _AUDIO_SOURCES]

    def _build_argv(self, session_id: int, audio: list[str], staging_dir: str, model: str) -> list[str]:
        argv = [
            "python", "-m", "epictrace.asr.worker",
            "--session", str(session_id),
            "--staging", staging_dir,
            "--model", model,
            "--sources", *audio,
        ]
        return argv

    def start(self, session_id: int, sources: list[str], staging_dir: str,
              *, model: str = "large-v3") -> None:
        """仅当 sources 含音频源时拉起 worker;否则 no-op。重复 start 同一 session 先停旧的。"""
        audio = self._audio_sources(sources)
        if not audio:
            return
        if session_id in self._procs:
            self.stop(session_id)
        argv = self._build_argv(session_id, audio, staging_dir, model)
        proc = self._spawn(argv)
        self._procs[session_id] = _Entry(proc=proc, sources=audio,
                                         staging_dir=staging_dir, model=model)

    def stop(self, session_id: int) -> None:
        """停掉该 session 的 worker(若有)。terminate 失败只记日志,不抛(停止不应连带失败)。"""
        entry = self._procs.pop(session_id, None)
        if entry is None:
            return
        try:
            entry.proc.terminate()
        except Exception as e:  # noqa: BLE001 — 停止尽力而为
            _log.warning("ASR worker terminate failed for session %s: %s", session_id, e)

    def pause(self, session_id: int) -> None:
        """暂停 = 停掉 worker(下游 confirmed/partial 自然停);恢复时按原参数重启。"""
        entry = self._procs.get(session_id)
        if entry is None:
            return
        # 记住重启所需参数后停掉进程;sources/staging/model 暂存在 _paused。
        self._paused[session_id] = (entry.sources, entry.staging_dir, entry.model)
        self.stop(session_id)

    def resume(self, session_id: int) -> None:
        """恢复:按 pause 时记下的参数重新拉起 worker。"""
        saved = self._paused.pop(session_id, None)
        if saved is None:
            return
        sources, staging_dir, model = saved
        self.start(session_id, sources, staging_dir, model=model)
