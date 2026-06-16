from epictrace.asr.config import AsrConfig


def test_defaults_are_weak_audio_friendly():
    c = AsrConfig()
    assert c.model == "large-v3"
    assert c.language == "zh"
    assert c.vad is True
    assert c.condition_prev is False        # 防幻觉沿前文传染
    assert c.force_confirm_after == 4
    assert c.no_speech == 0.6 and c.log_prob == -1.0 and c.compression_ratio == 2.4


def test_from_dict_overrides():
    c = AsrConfig.from_dict({"model": "medium", "vad": False})
    assert c.model == "medium" and c.vad is False and c.language == "zh"


def test_input_device_defaults_none_and_roundtrips():
    # 默认 None(系统默认输入设备);from_dict/to_dict 往返保住显式索引。
    assert AsrConfig().input_device is None
    c = AsrConfig.from_dict({"input_device": 2})
    assert c.input_device == 2
    assert c.to_dict()["input_device"] == 2


def test_window_seconds_default_and_roundtrips():
    # STEP 1:有界滑窗默认 28s;from_dict/to_dict 往返保住显式值。
    assert AsrConfig().window_seconds == 28.0
    c = AsrConfig.from_dict({"window_seconds": 12.0})
    assert c.window_seconds == 12.0
    assert c.to_dict()["window_seconds"] == 12.0


def test_compute_type_default_and_roundtrips():
    # STEP 3:CPU 上 int8_float32 比纯 int8 精度更好,作默认;from_dict 可切换。
    assert AsrConfig().compute_type == "int8_float32"
    c = AsrConfig.from_dict({"compute_type": "float32"})
    assert c.compute_type == "float32"
    assert c.to_dict()["compute_type"] == "float32"
