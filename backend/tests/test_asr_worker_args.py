import json

from epictrace.asr.config import AsrConfig
from epictrace.asr.worker import WorkerArgs, parse_args


def test_parse_args_basic():
    args = parse_args(["--session", "42", "--staging", "/tmp/42",
                       "--model", "medium", "--sources", "mic", "system_audio"])
    assert isinstance(args, WorkerArgs)
    assert args.session_id == 42
    assert args.staging_dir == "/tmp/42"
    assert args.model == "medium"
    assert args.sources == ["mic", "system_audio"]


def test_parse_args_default_model():
    args = parse_args(["--session", "1", "--staging", "/tmp/1", "--sources", "mic"])
    assert args.model == "large-v3"
    assert args.sources == ["mic"]
    # 无 --config → 落 AsrConfig 默认。
    assert isinstance(args.config, AsrConfig)
    assert args.config.model == "large-v3"


def test_parse_args_config_json_builds_full_asrconfig():
    """FIX D:--config <json> 回程成带非默认值的完整 AsrConfig(不只 model)。"""
    cfg_json = json.dumps({"model": "small", "vad": False, "vad_threshold": 0.3,
                           "force_confirm_after": 9})
    args = parse_args(["--session", "2", "--staging", "/tmp/2", "--model", "small",
                       "--config", cfg_json, "--sources", "mic"])
    assert args.config.model == "small"
    assert args.config.vad is False
    assert args.config.vad_threshold == 0.3
    assert args.config.force_confirm_after == 9


def test_wav_path_is_unique_per_launch():
    """FIX F:每次拉起用带时间戳的唯一文件名,pause(=停+重启)不覆盖暂停前音频;
    名字仍匹配 OrganizeService 的 audio-*.wav glob。"""
    import fnmatch

    from epictrace.asr.worker import _wav_path

    p = _wav_path("/tmp/9", "mic")
    name = p.rsplit("/", 1)[-1]
    assert fnmatch.fnmatch(name, "audio-*.wav")
    assert name.startswith("audio-mic-")


def test_shutdown_stops_sources_and_closes_wavs():
    """FIX B:收尾函数停所有源 + 关所有 wav;一处异常不漏其余。"""
    from epictrace.asr.worker import _shutdown

    events = []

    class _S:
        def __init__(self, name):
            self.name = name

        def stop(self):
            events.append(f"stop:{self.name}")

    class _W:
        def __init__(self, name, boom=False):
            self.name = name
            self.boom = boom

        def close(self):
            if self.boom:
                raise OSError("close boom")
            events.append(f"close:{self.name}")

    _shutdown({"mic": _S("mic"), "device": _S("device")},
              {"mic": _W("mic", boom=True), "device": _W("device")})
    assert "stop:mic" in events and "stop:device" in events
    # mic wav close 抛错被吞,device wav 仍关闭。
    assert "close:device" in events
