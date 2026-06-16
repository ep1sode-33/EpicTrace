import importlib.util

import pytest

# audio_sources 顶层 import numpy(轻);sounddevice/Popen 只在 source 运行时用。
# numpy 是 faster-whisper/onnxruntime 的传递依赖,装了 ASR 依赖即有;没有则跳过本组。
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("numpy") is None,
    reason="numpy not installed (comes with ASR deps; opt-in)",
)


def test_rms_normalize_boosts_weak_audio():
    import numpy as np

    from epictrace.asr.audio_sources import rms_normalize

    weak = (np.ones(1600, dtype=np.float32) * 0.01)
    out = rms_normalize(weak, target_dbfs=-20.0, max_gain=30.0)
    # 弱音被抬高(RMS 接近目标),但不超增益上限
    assert float(np.sqrt(np.mean(out ** 2))) > float(np.sqrt(np.mean(weak ** 2)))
    assert float(np.max(np.abs(out))) <= 1.0  # 不削顶溢出


def test_rms_normalize_silence_is_noop():
    import numpy as np

    from epictrace.asr.audio_sources import rms_normalize

    silence = np.zeros(800, dtype=np.float32)
    out = rms_normalize(silence, target_dbfs=-20.0, max_gain=30.0)
    # 近零能量不放大(否则把底噪轰起来);原样返回
    assert np.array_equal(out, silence)


def test_rms_normalize_respects_max_gain():
    import numpy as np

    from epictrace.asr.audio_sources import rms_normalize

    very_weak = np.ones(1600, dtype=np.float32) * 1e-4
    out = rms_normalize(very_weak, target_dbfs=-20.0, max_gain=20.0)
    # 极弱音需 >20dB 增益才到目标 → 被增益上限钳住(放大但远未达目标)
    applied = float(np.max(out) / np.max(very_weak))
    assert applied <= 10.0 ** (20.0 / 20.0) + 1e-3  # <= 10x(20dB)


def test_ring_buffer_pending_seconds():
    import numpy as np

    from epictrace.asr.audio_sources import RingBuffer

    rb = RingBuffer(sample_rate=16000)
    assert rb.pending_seconds() == 0.0
    rb.push(np.zeros(8000, dtype=np.float32))  # 0.5s @ 16k
    assert abs(rb.pending_seconds() - 0.5) < 1e-6
    out = rb.read()
    assert out.shape[0] == 8000
    # read 不清空(累积窗口);pending 反映已累积总秒数
    assert rb.pending_seconds() >= 0.0


def test_ring_buffer_base_offset_increments_after_truncation():
    import numpy as np

    from epictrace.asr.audio_sources import RingBuffer

    # 极小窗口(0.5s @ 16k = 8000 样本)便于逼出截断。
    rb = RingBuffer(sample_rate=16000, max_seconds=0.5)
    assert rb.base_offset() == 0.0
    rb.push(np.zeros(8000, dtype=np.float32))  # 恰好填满,未超 → 不截断
    assert rb.base_offset() == 0.0
    assert abs(rb.available_seconds() - 0.5) < 1e-6
    # 再 push 0.5s → 总 1.0s 超过窗口,丢弃最旧 0.5s,base_offset 前移 0.5s。
    rb.push(np.zeros(8000, dtype=np.float32))
    assert abs(rb.base_offset() - 0.5) < 1e-6
    # available_seconds = base_offset + len(buffer)/sr = 0.5 + 0.5 = 1.0(绝对末端不丢)
    assert abs(rb.available_seconds() - 1.0) < 1e-6


def test_system_audio_permission_line_parsing():
    from epictrace.asr.audio_sources import _is_permission_denied_line

    assert _is_permission_denied_line("PERMISSION_DENIED: tap failed") is True
    assert _is_permission_denied_line("warn: permission_denied (cached)") is True
    assert _is_permission_denied_line("started tap, 2 channels") is False


def test_ring_buffer_window_from_abs():
    import numpy as np

    from epictrace.asr.audio_sources import RingBuffer

    rb = RingBuffer(sample_rate=16000, max_seconds=10.0)
    sig = np.arange(16000, dtype=np.float32)  # 1s,值=样本下标便于核对切片
    rb.push(sig)
    # 从绝对 0.5s(=8000 样本)起切到末尾
    win = rb.window_from(0.5)
    assert win.shape[0] == 8000
    assert win[0] == 8000.0
