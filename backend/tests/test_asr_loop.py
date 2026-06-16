from epictrace.asr.config import AsrConfig
from epictrace.asr.loop import StreamLoop
from epictrace.asr.types import TranscriptSegment


class _FakeEngine:
    """按 (source, clip_start) 返回预设段。"""
    def __init__(self, script): self.script = script; self.seen = []
    def transcribe_window(self, pcm, *, clip_start, prefix, source, language=None):
        self.seen.append((source, clip_start))
        return self.script.get((source, round(clip_start, 1)), [])


def _seg(t, s, e, src):
    return TranscriptSegment(text=t, start=s, end=e, source=src, words=[], confirmed=False)


def test_alternates_to_source_with_more_pending_audio():
    eng = _FakeEngine({("device", 0.0): [_seg("讲座内容", 0, 2, "device"), _seg("x", 2, 3, "device")]})
    confirmed = []
    loop = StreamLoop(eng, AsrConfig(), on_confirmed=lambda s: confirmed.append(s), on_partial=lambda s: None)
    # device 路有 3s 待处理,mic 只 0.5s → 选 device
    loop.set_pending(mic=0.5, device=3.0)
    loop.tick(audio={"mic": b"", "device": b""})
    assert eng.seen[0][0] == "device"
    assert any(c.source == "device" and "讲座内容" in c.text for c in confirmed)
