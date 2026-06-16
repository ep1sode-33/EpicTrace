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

    closed = {"stopped": 0, "wav_closed": 0}

    class _FakeSource:
        def __init__(self, *a, **k):
            pass
        def start(self):
            pass
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


def test_active_channels_from_muted():
    """Feature B:由「已起的 worker 通道集」+「前端静音源 id 列表」算出仍活跃的通道。

    前端源 id(mic/system_audio)→ worker 通道(mic/device);静音的源对应的通道被剔除,
    其余保留。未起的源即便出现在静音列表里也无副作用。"""
    from epictrace.asr.worker import active_channels

    started = {"mic", "device"}
    # 无静音 → 全活跃。
    assert active_channels(started, []) == {"mic", "device"}
    # 静音 system_audio(→device)→ 只剩 mic。
    assert active_channels(started, ["system_audio"]) == {"mic"}
    # 静音 mic → 只剩 device。
    assert active_channels(started, ["mic"]) == {"device"}
    # 两路都静音 → 空集。
    assert active_channels(started, ["mic", "system_audio"]) == set()
    # 静音了一个根本没起的源 → 不影响已起的。
    assert active_channels({"mic"}, ["system_audio"]) == {"mic"}


def test_fetch_muted_returns_none_on_failure(monkeypatch):
    """FIX A:GET asr-mute 失败回 None(非 []),供调用方保留上次已知静音集、不误恢复全部。"""
    import urllib.error

    from epictrace.asr import worker

    def _boom(*a, **k):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(worker.urllib.request, "urlopen", _boom)
    assert worker._fetch_muted(7) is None


def test_apply_mute_transition_advances_offsets_and_cursors():
    """FIX A:某路 active→muted 时,推进其 wav 写游标到当前样本数 + ASR 游标到 available_seconds,
    使静音区间既不落 wav 也不被转写;unmute 后从「现在」继续。仅对「转静音」的通道动作。"""
    import numpy as np

    from epictrace.asr.worker import apply_mute_transition

    class _Src:
        def __init__(self, samples, avail):
            self._samples = samples
            self._avail = avail
        def read(self):
            return np.zeros(self._samples, dtype="float32")
        def available_seconds(self):
            return self._avail

    class _Loop:
        def __init__(self):
            self.skipped = []
        def skip_channel_to(self, ch, secs):
            self.skipped.append((ch, secs))

    sources = {"mic": _Src(48000, 30.0), "device": _Src(16000, 20.0)}
    written = {"mic": 0, "device": 16000}  # device 已写齐,mic 落后
    loop = _Loop()
    # mic active→muted(从 {mic,device} 变成 {device});device 仍活跃。
    apply_mute_transition({"mic", "device"}, {"device"}, sources, written, loop)
    # mic 的 wav 写游标推进到当前样本数(跳过将静音的 backlog,unmute 不回填)。
    assert written["mic"] == 48000
    # mic 的 ASR 游标跳到 available_seconds(静音区间不被转写)。
    assert loop.skipped == [("mic", 30.0)]
    # device 未转静音 → 不动它的写游标。
    assert written["device"] == 16000


def test_apply_mute_transition_noop_on_unmute():
    """FIX A:某路 muted→active(取消静音)不该触发跳游标/改写偏移——只在「转静音」时动作。"""
    import numpy as np

    from epictrace.asr.worker import apply_mute_transition

    class _Src:
        def read(self):
            return np.zeros(1000, dtype="float32")
        def available_seconds(self):
            return 5.0

    class _Loop:
        def __init__(self):
            self.skipped = []
        def skip_channel_to(self, ch, secs):
            self.skipped.append((ch, secs))

    sources = {"mic": _Src()}
    written = {"mic": 0}
    loop = _Loop()
    # mic muted→active:不在「转静音」集合 → 无副作用。
    apply_mute_transition({"mic"}, {"mic"}, sources, written, loop)
    apply_mute_transition(set(), {"mic"}, sources, written, loop)
    assert loop.skipped == []
    assert written["mic"] == 0


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
