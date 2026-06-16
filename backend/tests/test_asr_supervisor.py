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
