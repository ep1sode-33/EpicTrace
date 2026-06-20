from epictrace.asr.config import AsrConfig
from epictrace.asr.engine import FasterWhisperEngine


class _FakeWord:
    def __init__(self, w, s, e): self.word, self.start, self.end = w, s, e


class _FakeSeg:
    def __init__(self, text, s, e, words): self.text, self.start, self.end, self.words = text, s, e, words


class _FakeModel:
    def __init__(self): self.calls = []
    def transcribe(self, pcm, **opts):
        self.calls.append(opts)
        segs = [_FakeSeg(" 你好", 0.0, 1.0, [_FakeWord("你好", 0.0, 1.0)])]
        info = type("I", (), {"language": "zh"})()
        return iter(segs), info


def test_engine_maps_segments_and_passes_options():
    model = _FakeModel()
    eng = FasterWhisperEngine(model, AsrConfig())
    out = eng.transcribe_window(b"pcm", prefix="上一句", source="mic")
    assert out[0].text == " 你好" and out[0].source == "mic"
    assert out[0].words[0].word == "你好"
    opts = model.calls[0]
    # 改为「手动切片 + clip_timestamps='0'」:VAD 仅在 clip_timestamps=='0' 时生效
    # (faster-whisper 1.2.1,见 engine.py 注释),故绝不再传会话绝对 clip。
    assert opts["clip_timestamps"] == "0"
    assert opts["vad_filter"] is True
    # 词级 agreement:prefix(上轮 anchor 词)当 initial_prompt 喂回维持上下文(对齐参考产品
    # prefixTokens=lastAgreedWords)。condition_on_previous_text 仍关(不回注整轮历史)。
    assert opts["initial_prompt"] == "上一句"
    assert opts["word_timestamps"] is True
    # 默认 language=auto → auto_language 归一成 None(让 whisper 自动检测语言,中/英都识别)。
    assert opts["language"] is None
    assert opts["condition_on_previous_text"] is False


def test_prefix_seeds_initial_prompt():
    """词级 agreement:非空 prefix(anchor 词)作 initial_prompt 喂回;空 prefix → None。"""
    model = _FakeModel()
    eng = FasterWhisperEngine(model, AsrConfig())
    eng.transcribe_window(b"pcm", prefix="们", source="mic")
    assert model.calls[0]["initial_prompt"] == "们"
    eng.transcribe_window(b"pcm", prefix="", source="mic")
    assert model.calls[1]["initial_prompt"] is None


class _TradModel:
    """假件:返回繁体 segment + 词,用于验证引擎产文本被 t2s 转简。"""
    def transcribe(self, pcm, **opts):
        segs = [_FakeSeg("這個飄誤", 0.0, 1.0,
                         [_FakeWord("這個", 0.0, 0.5), _FakeWord("飄誤", 0.5, 1.0)])]
        return iter(segs), type("I", (), {"language": "zh"})()


def test_engine_simplifies_traditional_output():
    """rank1:large-v3 常吐繁体 → 引擎对 segment.text + 每个 word.word 过 t2s 转简;
    词数与 start/end 原样保留(时间戳对齐不破)。"""
    import pytest
    pytest.importorskip("opencc")
    eng = FasterWhisperEngine(_TradModel(), AsrConfig())
    out = eng.transcribe_window(b"pcm", prefix="", source="mic")
    assert out[0].text == "这个飘误"
    assert [w.word for w in out[0].words] == ["这个", "飘误"]
    assert len(out[0].words) == 2
    assert out[0].words[0].start == 0.0 and out[0].words[1].end == 1.0


def test_engine_passes_vad_min_speech_and_full_temperature_ladder():
    """rank5b/rank7:全温度阶梯 + VAD min_speech_duration_ms 透传到引擎。"""
    model = _FakeModel()
    eng = FasterWhisperEngine(model, AsrConfig())
    eng.transcribe_window(b"pcm", prefix="", source="mic")
    opts = model.calls[0]
    assert opts["temperature"] == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    assert opts["vad_parameters"]["min_speech_duration_ms"] == 250
