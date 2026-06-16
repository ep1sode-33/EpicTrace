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
    """history 多但已扫描追平的源不被选;另一路有未扫描音频才被选(FIX B 用 scanned_end)。"""
    eng = _FakeEngine({"mic": [_seg("新内容", 0, 2, "mic"), _seg("x", 2, 3, "mic")]})
    confirmed = []
    loop = StreamLoop(eng, AsrConfig(),
                      on_confirmed=confirmed.append, on_partial=lambda s: None)
    # device:available 100s 历史很长,但 scanned_end 已推到 100 → 未扫描 0s,不该选。
    # mic:available 5s,scanned 在 2s → 未扫描 3s,应被选。
    loop.set_sources({
        "mic": _FakeSource(base=0.0, available=5.0),
        "device": _FakeSource(base=0.0, available=100.0),
    })
    loop._states["device"].scanned_end = 100.0
    loop._states["mic"].scanned_end = 2.0
    loop.tick()
    assert eng.seen and eng.seen[0][0] == "mic"


def test_silent_source_scanned_then_other_source_not_starved():
    """FIX B:某路反复返回 0 段(静音/VAD 空)→ scanned_end 推进 → 其未扫描量降到 0 →
    调度不再霸占它,另一路(有未处理音频)被选,不被饿死;且同段不无限重解码。"""
    # mic 永远返回 0 段(静音);device 有真实内容。
    eng = _FakeEngine({"mic": [], "device": [_seg("讲座", 0, 2, "device"), _seg("x", 2, 3, "device")]})
    confirmed = []
    loop = StreamLoop(eng, AsrConfig(),
                      on_confirmed=confirmed.append, on_partial=lambda s: None)
    mic = _FakeSource(base=0.0, available=10.0)    # 未扫描 10s(初始)
    device = _FakeSource(base=0.0, available=5.0)  # 未扫描 5s
    loop.set_sources({"mic": mic, "device": device})
    # 第一轮:mic 未处理更多(10 > 5)→ 被选,但 0 段。scanned_end 应推进到 mic 末端。
    loop.tick()
    assert eng.seen[0][0] == "mic"
    assert loop._states["mic"].scanned_end == 10.0      # 0 段也推进扫描游标
    assert loop._unprocessed("mic") == 0.0              # 未扫描量归零
    # 第二轮:mic 未扫描 0(< _MIN_PENDING)→ device(5s)被选,不被饿死。
    loop.tick()
    assert eng.seen[1][0] == "device"
    assert any(c.source == "device" for c in confirmed)


def test_unprocessed_uses_scanned_not_confirmed_cursor():
    """FIX B:_unprocessed(调度)按 scanned_end 算,不按 last_confirmed_end。
    某路确认游标停在 0(去重拒/未确认)但已扫描到末端 → 调度视为无未处理量。"""
    eng = _FakeEngine({"mic": []})
    loop = StreamLoop(eng, AsrConfig(),
                      on_confirmed=lambda s: None, on_partial=lambda s: None)
    src = _FakeSource(base=0.0, available=8.0)
    loop.set_sources({"mic": src})
    loop._states["mic"].last_confirmed_end = 0.0
    loop._states["mic"].scanned_end = 8.0   # 已扫描到末端
    assert loop._unprocessed("mic") == 0.0  # 按 scanned_end 算 → 0


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


def test_slice_start_bounded_by_window_seconds():
    """STEP 1:cursor 远落后于 tail(120s 未确认)→ slice 起点被 window_seconds 上界夹住,
    只喂引擎尾部 ~window_seconds,而非整段 120s。"""
    eng = _FakeEngine({"mic": []})
    cfg = AsrConfig.from_dict({"window_seconds": 10.0})
    loop = StreamLoop(eng, cfg, on_confirmed=lambda s: None, on_partial=lambda s: None)
    # mic:base 0,available 120,cursor 0 → 未处理 120s;但窗口上界让切片只从 110s 起。
    src = _FakeSource(base=0.0, available=120.0)
    loop.set_sources({"mic": src, "device": _FakeSource(base=0.0, available=0.0)})
    loop.tick()
    # slice_start_abs = max(cursor=0, available-window=110, base=0) = 110
    assert src.windowed_from[-1] == 110.0


def test_window_seconds_never_exceeds_base_offset_clamp():
    """STEP 1:窗口上界与 base_offset 下界同时作用时取最大(切片仍不越缓冲头)。"""
    eng = _FakeEngine({"mic": []})
    cfg = AsrConfig.from_dict({"window_seconds": 10.0})
    loop = StreamLoop(eng, cfg, on_confirmed=lambda s: None, on_partial=lambda s: None)
    # base 5(缓冲头);available 8 → available-window = -2 < base → 取 base 5(不越缓冲头)。
    src = _FakeSource(base=5.0, available=8.0)
    loop.set_sources({"mic": src, "device": _FakeSource(base=5.0, available=5.0)})
    loop.tick()
    assert src.windowed_from[-1] == 5.0


def test_soft_force_confirm_keeps_cursor_within_window_of_tail():
    """STEP 1:每轮喂的切片永远 ≤ ~window_seconds,且游标永不落后 tail 超过 window_seconds。

    源 available 一路涨到 120s,引擎每轮都只给一段 partial(不自然确认);软强制确认应
    推进最早 pending 段,使 (available - cursor) 收敛在 window_seconds 内。"""
    cfg = AsrConfig.from_dict({"window_seconds": 10.0})
    confirmed = []
    # 引擎:每轮返回一段覆盖切片头部 ~2s 的 partial(slice-相对 0..2)。
    eng = _FakeEngine({"mic": [_seg("一句话", 0.0, 2.0, "mic")]})
    loop = StreamLoop(eng, cfg, on_confirmed=confirmed.append, on_partial=lambda s: None)
    src = _FakeSource(base=0.0, available=0.0)
    loop.set_sources({"mic": src, "device": _FakeSource(base=0.0, available=0.0)})
    max_slice_span = 0.0
    for step in range(60):
        src._available = float((step + 1) * 2)  # tail 每轮涨 2s,直到 120s
        before = loop._states["mic"].last_confirmed_end
        loop.tick()
        # 本轮切片跨度 = tail - slice_start_abs(slice_start = 上次记录的 windowed_from)。
        if src.windowed_from:
            span = src._available - src.windowed_from[-1]
            max_slice_span = max(max_slice_span, span)
        # 游标永不落后 tail 超过 window_seconds(+ 一轮 partial 余量)。
        lag = src._available - loop._states["mic"].last_confirmed_end
        assert lag <= cfg.window_seconds + 2.5 + 1e-6, f"step={step} lag={lag}"
        _ = before
    # 喂引擎的切片跨度始终 ≤ window_seconds(+ 小余量)。
    assert max_slice_span <= cfg.window_seconds + 2.0 + 1e-6
