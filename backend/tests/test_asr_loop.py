from epictrace.asr.config import AsrConfig
from epictrace.asr.loop import StreamLoop
from epictrace.asr.types import TranscriptSegment, WordTiming


class _FakeSource:
    """假音源:固定 base_offset / available_seconds,window_from 返回标记切片起点的张量。"""

    def __init__(self, *, base: float, available: float):
        self._base = base
        self._available = available
        self.windowed_from: list[float] = []

    def base_offset(self) -> float:
        return self._base

    def available_seconds(self) -> float:
        return self._available

    def window_from(self, abs_start: float):
        self.windowed_from.append(abs_start)
        return b"slice"


class _FakeEngine:
    """记录每次调用看到的 (source, pcm);返回脚本里按 source 给的 slice-相对段。"""

    def __init__(self, script):
        self.script = script
        self.seen = []

    def transcribe_window(self, pcm, *, prefix, source, language=None):
        self.seen.append((source, pcm))
        return self.script.get(source, [])


def _seg(t, s, e, src, words=None):
    return TranscriptSegment(text=t, start=s, end=e, source=src,
                             words=words or [], confirmed=False)


def test_alternates_to_source_with_more_unprocessed_audio():
    eng = _FakeEngine({"device": [_seg("讲座内容", 0, 2, "device"), _seg("x", 2, 3, "device")]})
    confirmed = []
    loop = StreamLoop(eng, AsrConfig(),
                      on_confirmed=confirmed.append, on_partial=lambda s: None)
    # device 路有 3s 未处理(base 0,available 3,cursor 0);mic 只 0.5s → 选 device
    loop.set_sources({
        "mic": _FakeSource(base=0.0, available=0.5),
        "device": _FakeSource(base=0.0, available=3.0),
    })
    loop.tick()
    assert eng.seen[0][0] == "device"
    assert any(c.source == "device" and "讲座内容" in c.text for c in confirmed)


def test_scheduler_ranks_by_unprocessed_not_raw_length():
    """history 多但 cursor 已追平的源不被选;另一路有未处理音频才被选(FIX C)。"""
    eng = _FakeEngine({"mic": [_seg("新内容", 0, 2, "mic"), _seg("x", 2, 3, "mic")]})
    confirmed = []
    loop = StreamLoop(eng, AsrConfig(),
                      on_confirmed=confirmed.append, on_partial=lambda s: None)
    # device:available 100s 历史很长,但 last_confirmed_end 已推到 100 → 未处理 0s,不该选。
    # mic:available 5s,cursor 在 2s → 未处理 3s,应被选。
    loop.set_sources({
        "mic": _FakeSource(base=0.0, available=5.0),
        "device": _FakeSource(base=0.0, available=100.0),
    })
    loop._states["device"].last_confirmed_end = 100.0
    loop._states["mic"].last_confirmed_end = 2.0
    loop.tick()
    assert eng.seen and eng.seen[0][0] == "mic"


def test_segments_shifted_to_absolute_time():
    """引擎返回 slice-相对段(start 0.5),slice_start_abs=10 → 确认段绝对 10.5(FIX A 平移)。"""
    eng = _FakeEngine({"mic": [
        _seg("第一句", 0.5, 2.0, "mic", words=[WordTiming("第一句", 0.5, 2.0)]),
        _seg("尾段", 2.0, 3.0, "mic"),  # 最后一段作 partial
    ]})
    confirmed = []
    partials = []
    loop = StreamLoop(eng, AsrConfig(),
                      on_confirmed=confirmed.append, on_partial=partials.append)
    # base_offset 10:缓冲已截断到绝对 10s 起;cursor 0 → slice_start_abs = max(0, 10) = 10。
    src = _FakeSource(base=10.0, available=13.0)
    loop.set_sources({"mic": src, "device": _FakeSource(base=10.0, available=10.0)})
    loop.tick()
    # 切片应从绝对 10.0 起
    assert src.windowed_from[-1] == 10.0
    # 确认段被平移到绝对时间:0.5 → 10.5,2.0 → 12.0;词级时间戳同样平移。
    assert confirmed and abs(confirmed[0].start - 10.5) < 1e-6
    assert abs(confirmed[0].end - 12.0) < 1e-6
    assert abs(confirmed[0].words[0].start - 10.5) < 1e-6
    # partial(最后一段)也平移:2.0 → 12.0
    assert partials and abs(partials[-1].start - 12.0) < 1e-6


def test_flush_drains_short_tail_below_min_pending():
    """FIX 3:某路只 0.5s 未处理(< _MIN_PENDING=1.0)→ tick() 不动,但 flush() 转写+确认它。"""
    eng = _FakeEngine({"mic": [_seg("短促一句", 0, 0.5, "mic")]})
    confirmed = []
    loop = StreamLoop(eng, AsrConfig(),
                      on_confirmed=confirmed.append, on_partial=lambda s: None)
    # mic 仅 0.5s 未处理(base 0,available 0.5,cursor 0)。
    loop.set_sources({
        "mic": _FakeSource(base=0.0, available=0.5),
        "device": _FakeSource(base=0.0, available=0.0),
    })
    # 普通 tick:0.5s < 1.0s 门 → 不选任何源、不转写。
    loop.tick()
    assert eng.seen == []
    assert confirmed == []
    # flush:短尾被转写,单段 partial 经 flush 强制确认 → emit。
    loop.flush()
    assert eng.seen and eng.seen[0][0] == "mic"
    assert any("短促一句" in c.text and c.confirmed for c in confirmed)


def test_flush_ignores_negligible_tail():
    """FIX 3:未处理音频极少(< ~0.2s)→ flush 不转写该路(避免空转/噪声段)。"""
    eng = _FakeEngine({"mic": [_seg("不该出现", 0, 0.1, "mic")]})
    confirmed = []
    loop = StreamLoop(eng, AsrConfig(),
                      on_confirmed=confirmed.append, on_partial=lambda s: None)
    loop.set_sources({
        "mic": _FakeSource(base=0.0, available=0.1),  # 仅 0.1s
        "device": _FakeSource(base=0.0, available=0.0),
    })
    loop.flush()
    assert eng.seen == []      # 太短,未转写
    assert confirmed == []


def test_flush_shifts_to_absolute_and_is_idempotent():
    """FIX 3:flush 转写结果平移回绝对时间;无新音频时二次 flush 不重复 emit(幂等)。"""
    eng = _FakeEngine({"mic": [_seg("尾段内容", 0.0, 0.5, "mic")]})
    confirmed = []
    loop = StreamLoop(eng, AsrConfig(),
                      on_confirmed=confirmed.append, on_partial=lambda s: None)
    src = _FakeSource(base=10.0, available=10.5)  # cursor 0 → slice_start_abs = max(0,10)=10
    loop.set_sources({"mic": src, "device": _FakeSource(base=10.0, available=10.0)})
    # device 游标已追平其末端 → 无未处理音频,flush 不碰它(只 mic 有 0.5s 短尾)。
    loop._states["device"].last_confirmed_end = 10.0
    loop.flush()
    assert src.windowed_from[-1] == 10.0
    assert confirmed and abs(confirmed[0].start - 10.0) < 1e-6  # 0.0 平移到 10.0
    n = len(confirmed)
    # 二次 flush:游标已追平 available,无未处理音频 + partial 已清 → 不再 emit。
    eng.seen.clear()
    loop.flush()
    assert eng.seen == []
    assert len(confirmed) == n


def test_slice_start_clamped_to_base_offset():
    """cursor 落后于 base_offset(那段已滚出缓冲)→ slice_start_abs 取 base_offset。"""
    eng = _FakeEngine({"mic": []})
    loop = StreamLoop(eng, AsrConfig(),
                      on_confirmed=lambda s: None, on_partial=lambda s: None)
    src = _FakeSource(base=20.0, available=25.0)
    loop.set_sources({"mic": src, "device": _FakeSource(base=20.0, available=20.0)})
    loop._states["mic"].last_confirmed_end = 5.0  # 早于 base_offset 20
    loop.tick()
    assert src.windowed_from[-1] == 20.0
