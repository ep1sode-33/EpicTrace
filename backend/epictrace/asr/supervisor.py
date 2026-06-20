from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

_log = logging.getLogger("epictrace")

# 停 worker 时 terminate 后等多久再强杀(秒)。worker 收 SIGTERM 后要 flush + 关 wav。
_STOP_KILL_TIMEOUT = 3.0

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
    config: dict = field(default_factory=dict)
    cache_dir: str | None = None


class AsrSupervisor:
    """后端侧:按 session 拉起/停止 ASR worker 子进程(faster-whisper 隔离在子进程,
    避开 embedder/Milvus 的 macOS fork 段错误)。只有选了 mic/system_audio 才起。

    spawn 注入便于测试(默认 subprocess.Popen)。pause/resume 用最简的「停 + 重启」实现:
    暂停即 terminate worker,恢复即按原参数重新 spawn(原始音频 wav 以追加方式续写)。
    """

    def __init__(self, *, spawn: Spawn | None = None) -> None:
        # None → start() 时回退到模块级 _default_spawn(在调用点解析,便于测试统一 monkeypatch
        # _default_spawn:即使 supervisor 在 patch 之前就构造好了,也不会 spawn 真子进程)。
        self._spawn = spawn
        self._procs: dict[int, _Entry] = {}
        # pause/resume 暂存:session_id -> (sources, staging_dir, model, config, cache_dir)
        self._paused: dict[int, tuple[list[str], str, str, dict, str | None]] = {}

    @staticmethod
    def _audio_sources(sources: list[str]) -> list[str]:
        # 保持调用方给定的顺序,仅保留识别的音频源。
        return [s for s in sources if s in _AUDIO_SOURCES]

    def _build_argv(self, session_id: int, audio: list[str], staging_dir: str,
                    model: str, config: dict, cache_dir: str | None) -> list[str]:
        argv = [
            "python", "-m", "epictrace.asr.worker",
            "--session", str(session_id),
            "--staging", staging_dir,
            # --model:仅休眠的 live 模式(worker._LIVE_TRANSCRIPTION=True)用;纯录音器忽略它。
            "--model", model,
            # 完整 ASR 设置(已由路由经 SettingsService 解析)以 JSON 透传,worker 据此建
            # AsrConfig.from_dict —— vad/阈值/force_confirm_after 等非默认值都生效(FIX D)。
            "--config", json.dumps(config or {}),
        ]
        # --cache-dir:faster-whisper download_root,仅休眠的 live 模式用(纯录音器不加载模型,忽略)。
        if cache_dir:
            argv += ["--cache-dir", cache_dir]
        argv += ["--sources", *audio]
        return argv

    def start(self, session_id: int, sources: list[str], staging_dir: str,
              *, model: str = "large-v3", config: dict | None = None,
              cache_dir: str | None = None) -> None:
        """仅当 sources 含音频源时拉起 worker;否则 no-op。重复 start 同一 session 先停旧的。

        config = 路由解析好的完整 ASR 设置 dict(SettingsService.get_asr_settings());透传给
        worker 建 AsrConfig。未传则空 dict(worker 落 AsrConfig 默认)。
        cache_dir = ASR 模型缓存目录,透传 worker 用于就绪检测 + WhisperModel download_root(FIX 2)。
        """
        audio = self._audio_sources(sources)
        if not audio:
            return
        cfg = dict(config or {})
        if session_id in self._procs:
            self.stop(session_id)
        argv = self._build_argv(session_id, audio, staging_dir, model, cfg, cache_dir)
        spawn = self._spawn or _default_spawn  # 调用点解析,便于测试 patch 模块级默认
        proc = spawn(argv)
        self._procs[session_id] = _Entry(proc=proc, sources=audio,
                                         staging_dir=staging_dir, model=model, config=cfg,
                                         cache_dir=cache_dir)

    def is_running(self, session_id: int) -> bool:
        """该 session 的 worker 是否仍在跑。用于中途启用音源时判定:不在跑则懒启动,
        在跑则只更新控制通道(worker 自行 reconcile 增删源)。

        worker 可能已自行退出(全部音源关闭 + 空闲超时自退)——entry 还在但进程已死,故用
        proc.poll() 判活(返回非 None = 已退);无 poll 的句柄(测试假件)按在跑处理。"""
        entry = self._procs.get(session_id)
        if entry is None:
            return False
        poll = getattr(entry.proc, "poll", None)
        if poll is None:
            return True
        return poll() is None

    def stop(self, session_id: int) -> None:
        """停掉该 session 的 worker(若有):terminate → wait(timeout)→ 仍活则 kill。

        terminate 发 SIGTERM,worker 收到后 flush 最后 confirmed 段 + 关 wav;给它有限时间
        优雅退出,超时才强杀。各步失败只记日志,不抛(停止尽力而为)。
        """
        entry = self._procs.pop(session_id, None)
        if entry is None:
            return
        proc = entry.proc
        try:
            proc.terminate()
        except Exception as e:  # noqa: BLE001 — 停止尽力而为
            _log.warning("ASR worker terminate failed for session %s: %s", session_id, e)
            return
        # 等优雅退出,超时强杀(wait/kill 是 Popen 接口;假件可不实现 → 容错跳过)。
        wait = getattr(proc, "wait", None)
        kill = getattr(proc, "kill", None)
        if wait is None or kill is None:
            return
        try:
            wait(timeout=_STOP_KILL_TIMEOUT)
        except Exception:  # noqa: BLE001 — TimeoutExpired 等 → 走强杀
            try:
                kill()
                wait(timeout=_STOP_KILL_TIMEOUT)
            except Exception as e:  # noqa: BLE001
                _log.warning("ASR worker kill failed for session %s: %s", session_id, e)

    def retranscribe(self, session_id: int, staging_dir: str, *,
                     config: dict | None = None, model: str | None = None) -> object | None:
        """会话停止后拉起一次性重转子进程(mlx 整文件重转 → 替换流式转录事件)。

        fire-and-forget:返回进程句柄(真件 Popen),不 track、不 wait(后台跑完自行退出,经
        POST /transcript 回写)。spawn 走同一注入点(测试假件不起真子进程)。失败由调用方吞。"""
        argv = ["python", "-m", "epictrace.asr.retranscribe",
                "--session", str(session_id), "--staging", staging_dir]
        if config:
            argv += ["--config", json.dumps(config)]
        if model:
            argv += ["--model", model]
        spawn = self._spawn or _default_spawn
        return spawn(argv)

    def pause(self, session_id: int) -> None:
        """暂停 = 停掉 worker(下游 confirmed/partial 自然停);恢复时按原参数重启。"""
        entry = self._procs.get(session_id)
        if entry is None:
            return
        # 记住重启所需参数后停掉进程;sources/staging/model/config/cache_dir 暂存在 _paused。
        self._paused[session_id] = (entry.sources, entry.staging_dir, entry.model,
                                    entry.config, entry.cache_dir)
        self.stop(session_id)

    def resume(self, session_id: int) -> None:
        """恢复:按 pause 时记下的参数重新拉起 worker。"""
        saved = self._paused.pop(session_id, None)
        if saved is None:
            return
        sources, staging_dir, model, config, cache_dir = saved
        self.start(session_id, sources, staging_dir, model=model, config=config,
                   cache_dir=cache_dir)
