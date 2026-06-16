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
