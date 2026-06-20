"""StreamLoop(锚定滑窗 + 词级 agreement 调度)测试。引擎/音源用假件,绝不起真 ASR/PortAudio。"""
import numpy as np

from epictrace.asr.config import AsrConfig
from epictrace.asr.loop import StreamLoop
from epictrace.asr.types import TranscriptSegment, WordTiming

SR = 16000


class _FakeSource:
    """假音源:固定 base_offset / available_seconds(测试可中途改 _available 模拟新音频),
    window_from 记录切起点并返回长度 = (available-abs_start)*SR 的 PCM(供断言 max_slice 截断)。
    默认 rms=0.05(高于能量门,放行转写);silent=True 时返回纯零(测能量门跳过)。"""

    def __init__(self, *, base=0.0, available=0.0, silent=False):
        self._base = base
        self._available = available
        self._fill = 0.0 if silent else 0.05
        self.windowed_from: list[float] = []

    def base_offset(self) -> float:
        return self._base

    def available_seconds(self) -> float:
        return self._available

    def window_from(self, abs_start: float):
        self.windowed_from.append(abs_start)
        n = max(0, int(round((self._available - abs_start) * SR)))
        return np.full(n, self._fill, dtype=np.float32)


class _FakeEngine:
    """记录每次调用 (source, prefix, pcm_len);按 source 的脚本逐次返回段(超出取最后一条)。"""

    def __init__(self, script):
        self.script = script          # {source: [resp_call0, resp_call1, ...]},resp = list[seg]
        self.seen: list[tuple] = []
        self._calls: dict[str, int] = {}

    def transcribe_window(self, pcm, *, prefix, source, language=None):
        k = self._calls.get(source, 0)
        self._calls[source] = k + 1
        self.seen.append((source, prefix, len(pcm) if hasattr(pcm, "__len__") else None))
        resp = self.script.get(source, [[]])
        return resp[min(k, len(resp) - 1)]


def _w(word, s, e):
    return WordTiming(word=word, start=s, end=e)


def _segw(spec, src):
    """按 [(word,start,end),...] 造一个带词级时间戳的段(slice-相对时间)。"""
    ws = [_w(*t) for t in spec]
    return TranscriptSegment(
        text="".join(w.word for w in ws),
        start=ws[0].start if ws else 0.0, end=ws[-1].end if ws else 0.0,
        source=src, words=ws, confirmed=False)


def _loop(eng, cfg=None, *, confirmed=None, partials=None):
    return StreamLoop(eng, cfg or AsrConfig(),
                      on_confirmed=(confirmed.append if confirmed is not None else (lambda s: None)),
                      on_partial=(partials.append if partials is not None else (lambda s: None)))


def test_picks_source_with_more_unprocessed_audio():
    eng = _FakeEngine({"device": [[_segw([("讲", 0, 1), ("座", 1, 2)], "device")]]})
    loop = _loop(eng)
    # device 3s 未处理(>=chunk 2.0);mic 仅 0.5s(<chunk)→ 选 device。
    loop.set_sources({"mic": _FakeSource(base=0.0, available=0.5),
                      "device": _FakeSource(base=0.0, available=3.0)})
    loop.tick()
    assert eng.seen and eng.seen[0][0] == "device"


def test_chunk_gate_blocks_sub_chunk_sources():
    """转写门 = chunk_seconds(2.0):两路都不足 2s 未处理 → tick 不选任何源(修掉旧 1.0s baseline)。"""
    eng = _FakeEngine({"mic": [[_segw([("x", 0, 1)], "mic")]]})
    loop = _loop(eng)
    loop.set_sources({"mic": _FakeSource(base=0.0, available=1.5),
                      "device": _FakeSource(base=0.0, available=1.0)})
    loop.tick()
    assert eng.seen == []


def test_words_and_partial_shifted_to_absolute_time():
    """引擎返回 slice-相对词(0.5s),base_offset=10 → 切片从绝对 10、partial 词平移到 10.5。"""
    eng = _FakeEngine({"mic": [[_segw([("第", 0.5, 2.0)], "mic")]]})
    partials = []
    loop = _loop(eng, partials=partials)
    src = _FakeSource(base=10.0, available=13.0)
    loop.set_sources({"mic": src, "device": _FakeSource(base=10.0, available=10.0)})
    loop.tick()
    assert src.windowed_from[-1] == 10.0
    assert partials and abs(partials[-1].start - 10.5) < 1e-6


def test_agreement_confirms_stable_prefix_and_feeds_anchor():
    """两/三轮稳定前缀:LCP 去末 anchor 确认,anchor 下轮当 prefix 回喂引擎。"""
    words1 = [("我", 0, 1), ("们", 1, 2)]
    words2 = [("我", 0, 1), ("们", 1, 2), ("去", 2, 3)]
    eng = _FakeEngine({"mic": [[_segw(words1, "mic")], [_segw(words2, "mic")],
                               [_segw(words2, "mic")]]})
    confirmed = []
    loop = _loop(eng, confirmed=confirmed)
    src = _FakeSource(base=0.0, available=3.0)
    loop.set_sources({"mic": src, "device": _FakeSource(base=0.0, available=0.0)})
    loop.tick()                       # call0:无 prev → 不确认
    assert confirmed == []
    src._available = 6.0              # 新音频到
    loop.tick()                       # call1:LCP=[我,们] → 确认「我」,anchor=们
    assert [c.text for c in confirmed] == ["我"]
    assert abs(confirmed[0].start) < 1e-6
    src._available = 9.0
    loop.tick()                       # call2:prefix 应为上轮 anchor「们」
    assert eng.seen[2][1] == "们"
    assert [c.text for c in confirmed] == ["我", "们"]


def test_silent_source_scanned_then_other_not_starved():
    """某路 0 段(静音)→ scanned_end 推进、未扫描归零,另一路被选,不被饿死。"""
    eng = _FakeEngine({"mic": [[]],
                       "device": [[_segw([("讲", 0, 1), ("座", 1, 2)], "device")]]})
    partials = []
    loop = _loop(eng, partials=partials)
    loop.set_sources({"mic": _FakeSource(base=0.0, available=10.0),
                      "device": _FakeSource(base=0.0, available=5.0)})
    loop.tick()                       # mic 未处理更多 → 选 mic,0 段
    assert eng.seen[0][0] == "mic"
    assert loop._unprocessed("mic") == 0.0   # scanned_end 推到末端,未扫描归零
    loop.tick()                       # device 被选,不饿死(首轮出 partial,词级确认需第二轮)
    assert eng.seen[1][0] == "device"
    assert any(p.source == "device" for p in partials)


def test_slice_capped_at_max_slice_on_cold_start():
    """冷启动 backlog 远超 max_slice → 单 tick 只解一窗 max_slice(不一次解整段 backlog)。"""
    eng = _FakeEngine({"mic": [[]]})
    cfg = AsrConfig.from_dict({"max_slice": 15.0, "slice_padding": 2.0})
    loop = _loop(eng, cfg)
    src = _FakeSource(base=0.0, available=90.0)
    loop.set_sources({"mic": src, "device": _FakeSource(base=0.0, available=0.0)})
    loop.tick()
    assert src.windowed_from[-1] == 0.0                 # 从缓冲头起
    assert eng.seen[0][2] == int(round(15.0 * SR))      # 截到 max_slice


def test_silent_backlog_not_stuck_seek_advances_cursor():
    """长静音 backlog:连续无词 → stall-seek 推进 last_agreed,不卡在同一段无限重解码。"""
    eng = _FakeEngine({"mic": [[]]})
    loop = _loop(eng)
    src = _FakeSource(base=0.0, available=90.0)
    loop.set_sources({"mic": src, "device": _FakeSource(base=0.0, available=0.0)})
    for _ in range(30):
        loop.tick()
    assert loop._states["mic"].last_agreed_seconds > 2.0   # 已 seek 前进


def test_flush_drains_short_tail_below_chunk():
    """短尾(<chunk)tick 不转,flush 强转 + 确认残尾。"""
    eng = _FakeEngine({"mic": [[_segw([("短", 0, 0.5)], "mic")]]})
    confirmed = []
    loop = _loop(eng, confirmed=confirmed)
    loop.set_sources({"mic": _FakeSource(base=0.0, available=0.5),
                      "device": _FakeSource(base=0.0, available=0.0)})
    loop.tick()
    assert eng.seen == []                       # 0.5 < chunk 2.0 → 不转
    loop.flush()
    assert eng.seen and eng.seen[0][0] == "mic"
    assert any("短" in c.text and c.confirmed for c in confirmed)


def test_flush_ignores_negligible_tail():
    eng = _FakeEngine({"mic": [[_segw([("不该", 0, 0.1)], "mic")]]})
    confirmed = []
    loop = _loop(eng, confirmed=confirmed)
    loop.set_sources({"mic": _FakeSource(base=0.0, available=0.1),
                      "device": _FakeSource(base=0.0, available=0.0)})
    loop.flush()
    assert eng.seen == []                        # 0.1 < _MIN_FLUSH_TAIL 0.2 → 不转
    assert confirmed == []


def test_flush_channel_only_flushes_that_channel():
    """flush_channel(idle) 只排空该路,不动另一路。"""
    eng = _FakeEngine({"mic": [[_segw([("尾", 0, 0.5)], "mic")]],
                       "device": [[_segw([("进", 0, 0.5)], "device")]]})
    confirmed = []
    loop = _loop(eng, confirmed=confirmed)
    loop.set_sources({"mic": _FakeSource(base=0.0, available=0.5),
                      "device": _FakeSource(base=0.0, available=0.5)})
    loop.flush_channel("mic")
    assert any(c.source == "mic" for c in confirmed)
    assert all(c.source != "device" for c in confirmed)


def test_reset_channel_clears_cursors():
    eng = _FakeEngine({"mic": [[_segw([("再", 0, 1), ("开", 1, 2)], "mic")]]})
    loop = _loop(eng)
    loop._states["mic"].last_agreed_seconds = 45.0
    loop._states["mic"].scanned_end = 45.0
    loop.reset_channel("mic")
    assert loop._states["mic"].last_agreed_seconds == 0.0
    assert loop._states["mic"].scanned_end == 0.0
    loop.reset_channel("nope")                   # 未知通道 no-op


def test_skip_channel_to_advances_both_cursors_monotonic():
    eng = _FakeEngine({"mic": [[]]})
    loop = _loop(eng)
    loop.skip_channel_to("mic", 30.0)
    assert loop._states["mic"].last_agreed_seconds == 30.0
    assert loop._states["mic"].scanned_end == 30.0
    loop.skip_channel_to("mic", 10.0)            # 单调,不回退
    assert loop._states["mic"].last_agreed_seconds == 30.0
    loop.skip_channel_to("nope", 5.0)            # 未知通道 no-op(不抛)


def test_silence_gate_skips_transcription_but_advances_scan():
    """能量门:近静音切片(纯零,如系统内录被拒吐零 / 安静时段)不喂引擎(防 mlx 静音幻觉 +
    空烧 GPU),但仍推进 scanned_end 使调度不卡在它。"""
    eng = _FakeEngine({"mic": [[_segw([("不该出现", 0, 2)], "mic")]]})
    loop = _loop(eng)
    loop.set_sources({"mic": _FakeSource(base=0.0, available=5.0, silent=True),
                      "device": _FakeSource(base=0.0, available=0.0)})
    loop.tick()
    assert eng.seen == []                              # 静音 → 引擎从未被调用
    assert loop._states["mic"].scanned_end > 0.0       # 但扫描游标推进(不霸占调度)


def test_slice_clamped_to_base_offset():
    """cursor 落后于 base_offset(那段已滚出缓冲)→ 切片起点取 base_offset。"""
    eng = _FakeEngine({"mic": [[]]})
    loop = _loop(eng)
    src = _FakeSource(base=20.0, available=25.0)
    loop.set_sources({"mic": src, "device": _FakeSource(base=20.0, available=20.0)})
    loop.tick()
    assert src.windowed_from[-1] == 20.0
