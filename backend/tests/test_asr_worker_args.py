import json

import pytest

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


def test_parse_args_cache_dir():
    """FIX 2:--cache-dir 透传到 WorkerArgs.cache_dir,供就绪检测 + download_root 用。"""
    args = parse_args(["--session", "1", "--staging", "/tmp/1",
                       "--cache-dir", "/data/.asr-models", "--sources", "mic"])
    assert args.cache_dir == "/data/.asr-models"


def test_parse_args_cache_dir_defaults_none():
    args = parse_args(["--session", "1", "--staging", "/tmp/1", "--sources", "mic"])
    assert args.cache_dir is None


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


def test_worker_main_fails_fast_when_model_absent(tmp_path, monkeypatch):
    """FIX 1(防御纵深):模型不在缓存里 → worker.main 直接退 1,绝不构建 WhisperModel
    去自动下载。用空 tmp 缓存目录 + 桩掉 _build_engine(若被调到就炸,证明没走到那)。"""
    import epictrace.asr.worker as worker

    def _boom_engine(*a, **k):
        raise AssertionError("WhisperModel must NOT be constructed when model absent")

    monkeypatch.setattr(worker, "_build_engine", _boom_engine)
    monkeypatch.setattr(worker, "_post", lambda *a, **k: None)  # 不真发网络
    monkeypatch.setattr(worker, "_mlx_ready", lambda *a, **k: False)  # 测 faster-whisper fail-fast 路
    monkeypatch.setattr(worker, "_LIVE_TRANSCRIPTION", True)  # 模型 fail-fast 只在 live 模式跑

    rc = worker.main([
        "--session", "1", "--staging", str(tmp_path),
        "--cache-dir", str(tmp_path / "empty-cache"), "--sources", "mic",
    ])
    assert rc == 1


def test_worker_main_shuts_down_sources_when_engine_build_fails(tmp_path, monkeypatch):
    """FIX E:模型在缓存里(过 fail-fast),但 _build_engine 抛错 → 已起的源被停、已开的 wav
    被关(统一 try/finally 收尾),绝不泄漏句柄。"""
    import sys
    import types

    import epictrace.asr.provisioner as prov
    import epictrace.asr.worker as worker

    # 过 fail-fast:假装模型存在(main 内部从 provisioner 局部 import,故 patch 源模块)。
    monkeypatch.setattr(prov, "detect_asr_model", lambda *a, **k: True)
    monkeypatch.setattr(worker, "_post", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_mlx_ready", lambda *a, **k: False)  # 走 faster-whisper 引擎构建路
    monkeypatch.setattr(worker, "_LIVE_TRANSCRIPTION", True)  # 引擎构建只在 live 模式发生

    closed = {"stopped": 0, "wav_closed": 0}

    class _FakeSource:
        def __init__(self, *a, **k):
            self.sample_rate = 48000  # worker 用 s.sample_rate 建 wav(48k 录音)
        def start(self):
            pass
        def read(self):
            import numpy as np
            return np.empty(0, dtype=np.float32)
        def stop(self):
            closed["stopped"] += 1

    class _FakeWav:
        def __init__(self, *a, **k):
            pass
        def write(self, *a, **k):
            pass
        def close(self):
            closed["wav_closed"] += 1

    # 桩掉运行时 import 的音频依赖:soundfile + audio_sources(避免真 PortAudio/真文件)。
    fake_audio = types.ModuleType("epictrace.asr.audio_sources")
    fake_audio.SAMPLE_RATE = 16000
    fake_audio.MicSource = _FakeSource
    fake_audio.SystemAudioSource = _FakeSource
    monkeypatch.setitem(sys.modules, "epictrace.asr.audio_sources", fake_audio)
    fake_sf = types.ModuleType("soundfile")
    fake_sf.SoundFile = lambda *a, **k: _FakeWav()
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)

    # _build_engine 抛错(模型加载失败模拟)。
    def _boom(*a, **k):
        raise RuntimeError("model load boom")

    monkeypatch.setattr(worker, "_build_engine", _boom)

    with pytest.raises(RuntimeError, match="model load boom"):
        worker.main([
            "--session", "1", "--staging", str(tmp_path),
            "--cache-dir", str(tmp_path / "cache"), "--sources", "mic",
        ])
    # 关键:即便引擎构建抛错,源被停、wav 被关(统一收尾,无泄漏)。
    assert closed["stopped"] == 1
    assert closed["wav_closed"] == 1


def test_desired_channels_maps_source_ids():
    """动态音源:前端期望开启的源 id(mic/system_audio)→ worker 通道集(mic/device);未知源忽略。"""
    from epictrace.asr.worker import desired_channels

    assert desired_channels([]) == set()
    assert desired_channels(["mic"]) == {"mic"}
    assert desired_channels(["system_audio"]) == {"device"}
    assert desired_channels(["mic", "system_audio"]) == {"mic", "device"}
    # 未知源 id 忽略,不污染通道集。
    assert desired_channels(["mic", "bogus"]) == {"mic"}


def test_reconcile_channels_computes_start_stop():
    """动态音源:据期望开启通道集 vs 已启动集,算出 (要启动, 要停止)。"""
    from epictrace.asr.worker import reconcile_channels

    # 期望 {mic,device},已起 {mic} → 启动 device,无停止。
    assert reconcile_channels({"mic", "device"}, {"mic"}) == ({"device"}, set())
    # 期望 {mic},已起 {mic,device} → 无启动,停止 device(中途关源)。
    assert reconcile_channels({"mic"}, {"mic", "device"}) == (set(), {"device"})
    # 期望空,已起 {mic} → 停止 mic(全关)。
    assert reconcile_channels(set(), {"mic"}) == (set(), {"mic"})
    # 一致 → 无动作。
    assert reconcile_channels({"mic"}, {"mic"}) == (set(), set())


def test_idle_exit_due():
    """动态音源:无任何音源且持续 timeout 秒 → 该自退(模型随进程释放)。"""
    from epictrace.asr.worker import idle_exit_due

    # idle_since=None(当前有音源)→ 永不到期。
    assert idle_exit_due(None, 100.0, 60.0) is False
    # 空闲未满 timeout。
    assert idle_exit_due(10.0, 50.0, 60.0) is False
    # 空闲恰满 / 超过 timeout。
    assert idle_exit_due(10.0, 70.0, 60.0) is True
    assert idle_exit_due(10.0, 80.0, 60.0) is True


def test_fetch_enabled_returns_none_on_failure(monkeypatch):
    """网络抖动:GET asr-source 失败回 None(非 []),供调用方保留上次已知集、不误停所有源。"""
    import urllib.error

    from epictrace.asr import worker

    def _boom(*a, **k):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(worker.urllib.request, "urlopen", _boom)
    assert worker._fetch_enabled(7) is None


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


def test_build_engine_prefers_mlx_when_ready(monkeypatch):
    """live 引擎:_mlx_ready 为真 → _build_engine 返回 MlxWhisperEngine(不构建 faster-whisper)。"""
    import epictrace.asr.worker as worker
    from epictrace.asr.config import AsrConfig
    from epictrace.asr.mlx_engine import MlxWhisperEngine

    monkeypatch.setattr(worker, "_mlx_ready", lambda *a, **k: True)
    eng = worker._build_engine(AsrConfig(), "/tmp/cache")
    assert isinstance(eng, MlxWhisperEngine)


def test_build_engine_falls_back_to_faster_whisper(monkeypatch):
    """_mlx_ready 为假 → 走 faster-whisper 构建(WhisperModel 桩掉,不真加载)。"""
    import sys
    import types

    import epictrace.asr.worker as worker
    from epictrace.asr.config import AsrConfig

    monkeypatch.setattr(worker, "_mlx_ready", lambda *a, **k: False)
    built = {}

    class _FakeWM:
        def __init__(self, model, **k):
            built["model"] = model

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = _FakeWM
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)
    eng = worker._build_engine(AsrConfig(model="medium"), "/tmp/cache")
    from epictrace.asr.engine import FasterWhisperEngine
    assert isinstance(eng, FasterWhisperEngine) and built["model"] == "medium"
