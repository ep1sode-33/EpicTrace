"""一次性重转子进程的纯逻辑(parse_args + 文件名→通道)。不碰真 mlx/音频。"""
from epictrace.asr.retranscribe import RetranscribeArgs, channel_of, parse_args


def test_parse_args_basic():
    import json
    cfg = json.dumps({"language": "zh", "vad_threshold": 0.3})
    a = parse_args(["--session", "7", "--staging", "/tmp/7", "--config", cfg, "--model", "repo/x"])
    assert isinstance(a, RetranscribeArgs)
    assert a.session_id == 7 and a.staging_dir == "/tmp/7"
    assert a.config.vad_threshold == 0.3 and a.model == "repo/x"


def test_parse_args_defaults():
    a = parse_args(["--session", "1", "--staging", "/tmp/1"])
    assert a.model is None and a.config.language == "auto"


def test_channel_of():
    assert channel_of("audio-mic-1781717543.wav") == "mic"
    assert channel_of("audio-device-1781719965.wav") == "device"
    assert channel_of("weird.wav") == "mic"          # 不符合 → 回 mic
    assert channel_of("audio-bogus-1.wav") == "mic"  # 未知通道 → 回 mic


def test_wav_timestamp_parses_and_degrades():
    from epictrace.asr.retranscribe import wav_timestamp
    assert wav_timestamp("audio-device-1781724943.wav") == 1781724943
    assert wav_timestamp("audio-mic-1781717543.wav") == 1781717543
    assert wav_timestamp("weird.wav") is None
    assert wav_timestamp("audio-mic-notanumber.wav") is None


def test_session_offsets_places_pause_segments_in_timeline():
    """pause/resume 分段:各 wav 相对最早 wav 的秒偏移。最早段=0,后段按真实时间线推后。"""
    from epictrace.asr.retranscribe import session_offsets
    # 文件名是毫秒戳(int(time.time()*1000));偏移 = (戳 − 最早戳)/1000 秒。
    names = [
        "audio-device-1000000.wav",   # 会话起点(t0)
        "audio-mic-1000000.wav",      # 同时起的另一通道 → 也是 0
        "audio-device-1170000.wav",   # resume 后新段(+170s)
        "audio-device-1310000.wav",   # 再 resume(+310s)
    ]
    off = session_offsets(names)
    assert off["audio-device-1000000.wav"] == 0.0
    assert off["audio-mic-1000000.wav"] == 0.0
    assert off["audio-device-1170000.wav"] == 170.0
    assert off["audio-device-1310000.wav"] == 310.0
    # 解析不出时间戳 → 偏移 0(降级)。
    assert session_offsets(["weird.wav"])["weird.wav"] == 0.0
    assert session_offsets([]) == {}


def test_to_asr_16k_resamples_and_monos():
    """48k 录音(可能立体声)→ 16k 单声道 float32 喂 Whisper;16k 直通(仅合并声道)。"""
    import numpy as np

    from epictrace.asr.retranscribe import _to_asr_16k
    t = np.arange(48000) / 48000.0
    mono48 = (0.1 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    out = _to_asr_16k(mono48, 48000)                       # 48k → 16k
    assert out.ndim == 1 and out.dtype == np.float32 and 15900 <= len(out) <= 16100
    assert len(_to_asr_16k(mono48[:16000], 16000)) == 16000  # 16k 直通
    stereo = np.stack([mono48, mono48], axis=1)             # (n, 2)
    mono = _to_asr_16k(stereo, 48000)
    assert mono.ndim == 1 and 15900 <= len(mono) <= 16100   # 立体声 → 单声道 + 降采样
