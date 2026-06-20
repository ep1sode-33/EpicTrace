from epictrace.asr.config import AsrConfig


def test_defaults_are_weak_audio_friendly():
    c = AsrConfig()
    assert c.model == "large-v3"
    assert c.language == "auto"
    assert c.vad is True
    assert c.condition_prev is False        # 防幻觉沿前文传染
    assert c.force_confirm_after == 4
    assert c.no_speech == 0.6 and c.log_prob == -1.0 and c.compression_ratio == 2.4


def test_from_dict_overrides():
    c = AsrConfig.from_dict({"model": "medium", "vad": False})
    assert c.model == "medium" and c.vad is False and c.language == "auto"


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


def test_rms_normalize_off_by_default():
    # STEP 4:RMS 归一化默认关(放大噪声/损精度);用户仍可显式开。
    assert AsrConfig().rms_normalize is False
    assert AsrConfig.from_dict({"rms_normalize": True}).rms_normalize is True


def test_vad_min_speech_default_and_roundtrip():
    """rank7:VAD 最短语音块默认 250ms;from_dict/to_dict 往返保住显式值。"""
    from epictrace.asr.config import AsrConfig

    assert AsrConfig().vad_min_speech_ms == 250
    c = AsrConfig.from_dict({"vad_min_speech_ms": 150})
    assert c.vad_min_speech_ms == 150
    assert c.to_dict()["vad_min_speech_ms"] == 150


def test_repetition_lightly_on_by_default():
    """rank8:轻度复读惩罚作默认(解码层降 loop 概率),与文本层段内 loop 过滤互补。"""
    from epictrace.asr.config import AsrConfig

    assert AsrConfig().repetition_penalty == 1.1
    # no_repeat_ngram 保持 0:硬禁 n-gram 会压掉合法中文重复;段内 loop 由文本层 is_intra_segment_loop 兜。
    assert AsrConfig().no_repeat_ngram == 0


def test_auto_language_normalizes_to_none():
    """auto/空/None → None(whisper 自动检测);具体语言码原样返回。默认配置即 auto。"""
    from epictrace.asr.config import auto_language
    assert auto_language("auto") is None
    assert auto_language("") is None
    assert auto_language(None) is None
    assert auto_language("zh") == "zh"
    assert auto_language("en") == "en"
    assert AsrConfig().language == "auto"
