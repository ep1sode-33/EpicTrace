from epictrace.asr.supervisor import AsrSupervisor


def test_starts_worker_only_when_audio_source_selected():
    spawned = []
    sup = AsrSupervisor(spawn=lambda argv: spawned.append(argv) or object())
    sup.start(session_id=1, sources=["note", "clipboard"], staging_dir="/tmp/1")
    assert spawned == []                      # 无音频源 → 不起
    sup.start(session_id=2, sources=["mic"], staging_dir="/tmp/2")
    assert len(spawned) == 1 and "--session" in spawned[0]


def test_stop_terminates():
    procs = []

    class _P:
        def __init__(self):
            self.killed = False

        def terminate(self):
            self.killed = True

        def poll(self):
            return None

    sup = AsrSupervisor(spawn=lambda argv: procs.append(_P()) or procs[-1])
    sup.start(session_id=3, sources=["system_audio"], staging_dir="/tmp/3")
    sup.stop(3)
    assert procs[0].killed is True


def test_argv_carries_sources_staging_model():
    spawned = []
    sup = AsrSupervisor(spawn=lambda argv: spawned.append(argv) or object())
    sup.start(session_id=7, sources=["mic", "system_audio", "note"],
              staging_dir="/tmp/7", model="medium")
    argv = spawned[0]
    assert argv[:3] == ["python", "-m", "epictrace.asr.worker"]
    # 仅音频源被透传(note 不是音频源)
    assert "mic" in argv and "system_audio" in argv and "note" not in argv
    assert "/tmp/7" in argv and "medium" in argv


def test_argv_carries_cache_dir():
    """FIX 2:supervisor 把路由传入的 ASR 缓存目录以 --cache-dir 透传 worker,
    使 worker 的就绪检测 + WhisperModel(download_root) 与 provisioner 落盘同一目录。"""
    spawned = []
    sup = AsrSupervisor(spawn=lambda argv: spawned.append(argv) or object())
    sup.start(session_id=8, sources=["mic"], staging_dir="/tmp/8",
              model="medium", cache_dir="/data/.asr-models")
    argv = spawned[0]
    assert "--cache-dir" in argv
    assert argv[argv.index("--cache-dir") + 1] == "/data/.asr-models"


def test_argv_carries_full_config_json_roundtrips():
    """FIX D:supervisor 把完整 ASR 设置以 --config <json> 透传;worker.parse_args 回程出
    带非默认值的 AsrConfig(不只 model)。"""
    import json

    from epictrace.asr.worker import parse_args

    spawned = []
    sup = AsrSupervisor(spawn=lambda argv: spawned.append(argv) or object())
    settings = {"model": "medium", "vad": False, "vad_threshold": 0.25,
                "force_confirm_after": 7, "language": "en"}
    sup.start(session_id=11, sources=["mic"], staging_dir="/tmp/11",
              model="medium", config=settings)
    argv = spawned[0]
    assert "--config" in argv
    cfg_json = argv[argv.index("--config") + 1]
    assert json.loads(cfg_json)["vad_threshold"] == 0.25
    # worker.parse_args 把它回程成带非默认值的 AsrConfig。
    args = parse_args(argv[3:])  # 去掉 python -m epictrace.asr.worker
    assert args.config.model == "medium"
    assert args.config.vad is False
    assert args.config.vad_threshold == 0.25
    assert args.config.force_confirm_after == 7
    assert args.config.language == "en"


def test_stop_kills_after_wait_timeout():
    """FIX B:terminate → wait 超时 → kill。假件 wait 首次抛 TimeoutExpired,记录调用序。"""
    import subprocess

    events = []

    class _P:
        def __init__(self):
            self._waits = 0

        def terminate(self):
            events.append("terminate")

        def kill(self):
            events.append("kill")

        def wait(self, timeout=None):
            self._waits += 1
            events.append(f"wait{self._waits}")
            if self._waits == 1:
                raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)
            return 0

        def poll(self):
            return None

    procs = []
    sup = AsrSupervisor(spawn=lambda argv: procs.append(_P()) or procs[-1])
    sup.start(session_id=5, sources=["mic"], staging_dir="/tmp/5")
    sup.stop(5)
    # terminate → wait(超时) → kill → wait
    assert events == ["terminate", "wait1", "kill", "wait2"]


def test_stop_graceful_no_kill():
    """worker 在超时前优雅退出 → 不强杀。"""
    events = []

    class _P:
        def terminate(self):
            events.append("terminate")

        def kill(self):
            events.append("kill")

        def wait(self, timeout=None):
            events.append("wait")
            return 0

        def poll(self):
            return None

    procs = []
    sup = AsrSupervisor(spawn=lambda argv: procs.append(_P()) or procs[-1])
    sup.start(session_id=6, sources=["mic"], staging_dir="/tmp/6")
    sup.stop(6)
    assert events == ["terminate", "wait"]
    assert "kill" not in events


def test_is_running_reflects_proc_state():
    """动态音源懒启动判定:没起过 = 不在跑;起了 = 在跑;进程自退(poll 非 None)= 不在跑。"""
    class _P:
        def __init__(self):
            self.dead = False

        def terminate(self):
            self.dead = True

        def kill(self):
            self.dead = True

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0 if self.dead else None

    procs = []
    sup = AsrSupervisor(spawn=lambda argv: procs.append(_P()) or procs[-1])
    assert sup.is_running(3) is False            # 没起过
    sup.start(session_id=3, sources=["mic"], staging_dir="/tmp/3")
    assert sup.is_running(3) is True             # 起了
    procs[0].dead = True                         # 模拟 worker 自退(全关空闲超时)
    assert sup.is_running(3) is False            # poll 非 None → 不在跑
    # 自退后 stop 也不报错(entry 仍在,terminate 死进程无害)。
    sup.stop(3)
    assert sup.is_running(3) is False


def test_is_running_false_after_stop():
    class _P:
        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return None

    sup = AsrSupervisor(spawn=lambda argv: _P())
    sup.start(session_id=4, sources=["mic"], staging_dir="/tmp/4")
    assert sup.is_running(4) is True
    sup.stop(4)
    assert sup.is_running(4) is False            # entry 已 pop


def test_pause_resume_restarts_worker():
    events = []

    class _P:
        def terminate(self):
            events.append("terminate")

        def poll(self):
            return None

    def _spawn(argv):
        events.append("spawn")
        return _P()

    sup = AsrSupervisor(spawn=_spawn)
    sup.start(session_id=9, sources=["mic"], staging_dir="/tmp/9")
    sup.pause(9)
    sup.resume(9)
    # 起一次 → pause 停 → resume 再起
    assert events == ["spawn", "terminate", "spawn"]
