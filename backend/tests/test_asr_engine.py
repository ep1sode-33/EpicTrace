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
    # STEP 5:上下文策略二选一去重叠——condition_prev=False + 不再用 partial seed initial_prompt
    # (有界滑窗已自带声学上下文)。prefix 参数被忽略,initial_prompt 恒为 None。
    assert opts["initial_prompt"] is None
    assert opts["word_timestamps"] is True
    assert opts["language"] == "zh"
    assert opts["condition_on_previous_text"] is False


def test_prefix_no_longer_seeds_initial_prompt():
    """STEP 5:即便传了非空 prefix,initial_prompt 仍为 None(不把上轮文本注回下轮)。"""
    model = _FakeModel()
    eng = FasterWhisperEngine(model, AsrConfig())
    eng.transcribe_window(b"pcm", prefix="任何前文都不再注入", source="mic")
    assert model.calls[0]["initial_prompt"] is None
