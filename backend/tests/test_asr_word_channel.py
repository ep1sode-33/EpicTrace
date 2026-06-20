"""WordChannel(词级流式确认)单测:移植自参考产品生产算法的纯逻辑。"""
from epictrace.asr.hallucination import HallucinationFilter
from epictrace.asr.types import WordTiming
from epictrace.asr.word_channel import WordChannel, _meaningful


def _w(word, s, e):
    return WordTiming(word=word, start=s, end=e)


def _words(spec):
    return [_w(*t) for t in spec]


def _chan(**kw):
    return WordChannel("mic", HallucinationFilter(), **kw)


def test_first_ingest_no_confirm_sets_prev_and_partial():
    ch = _chan()
    out = ch.ingest(_words([("我", 0, 1), ("们", 1, 2)]), buffer_end=2.0)
    assert out == []                       # 首轮无 prev → 不确认
    assert ch.last_agreed_seconds == 0.0
    assert ch.partial is not None and ch.partial.text == "我们"


def test_stable_prefix_confirms_lcp_minus_anchor():
    ch = _chan(anchor_words=1)
    ch.ingest(_words([("我", 0, 1), ("们", 1, 2)]), buffer_end=2.0)
    out = ch.ingest(_words([("我", 0, 1), ("们", 1, 2), ("去", 2, 3)]), buffer_end=3.0)
    # prev=[我,们] current=[我,们,去] → LCP=[我,们],tc=1 → confirmed=[我],anchor=[们]
    assert [c.text for c in out] == ["我"]
    assert all(c.confirmed for c in out)
    assert ch.last_agreed_seconds == 1.0      # anchor[0].start = 们.start
    assert ch.last_confirmed_end == 1.0       # 我.end
    assert ch.anchor_text() == "们"           # anchor 喂回引擎当 prefix


def test_confirmed_hallucination_dropped_but_window_cursor_advances():
    ch = _chan(anchor_words=1)
    base = _words([("谢", 0, 1), ("谢", 1, 2), ("观", 2, 3), ("看", 3, 4), ("的", 4, 5)])
    ch.ingest(base + [_w("a", 5, 6)], buffer_end=6.0)
    out = ch.ingest(base + [_w("b", 5, 6)], buffer_end=6.0)
    # LCP=[谢,谢,观,看,的](5),tc=1 → confirmed=谢谢观看(幻觉)→ 不 emit;anchor=[的]
    assert out == []
    assert ch.last_agreed_seconds == 4.0      # 窗口游标仍推进到 anchor(的.start)
    assert ch.last_confirmed_end == 0.0       # 但确认末端不动(没真 emit)


def test_force_confirm_after_no_progress():
    ch = _chan(anchor_words=1, force_confirm_after=2)
    ch.ingest(_words([("甲", 0, 1), ("乙", 1, 2)]), buffer_end=2.0)
    ch.ingest(_words([("丙", 0, 1), ("丁", 1, 2)]), buffer_end=2.0)   # LCP=0 → no_advance=1
    out = ch.ingest(_words([("戊", 0, 1), ("己", 1, 2)]), buffer_end=2.0)  # no_advance=2 → force
    assert [c.text for c in out] == ["戊"]     # 强制确认 current 除末 anchor(己)外


def test_no_word_results_stall_seek_advances_cursor():
    ch = _chan(force_confirm_after=2, stall_seek=0.8)
    ch.ingest([], buffer_end=10.0)            # no_advance=1(<force,不 seek)
    assert ch.last_agreed_seconds == 0.0
    ch.ingest([], buffer_end=10.0)            # no_advance=2 → seek 一步 0.8
    assert abs(ch.last_agreed_seconds - 0.8) < 1e-6
    assert ch.partial is None


def test_seek_jumps_past_current_hypothesis_when_unconfirmable():
    """stall 且有 current hypothesis(确认不出)→ seek 跳过整个 hypothesis(>= 末词末端−0.05),
    不只挪一小步反复重解码同一片(对齐生产 candidateFromWords)。"""
    ch = _chan(force_confirm_after=2, stall_seek=0.8, max_slice=15.0, slice_padding=2.0)
    ch._consecutive_no_advance = 2
    ch._maybe_seek(20.0, _words([("a", 5, 6), ("b", 6, 7), ("c", 7, 8)]))
    assert abs(ch.last_agreed_seconds - 7.95) < 1e-6   # 8 − 0.05


def test_seek_fast_forwards_on_large_silent_lag():
    """无词且落后超一个滑窗(lag > maxSlice+padding)→ 快进到近 tail(buffer_end − maxSeekStep),
    否则长静音 backlog 只能 0.8s/次 龟速追(对齐生产 lag 分支)。"""
    ch = _chan(force_confirm_after=2, stall_seek=0.8, max_slice=15.0, slice_padding=2.0)
    ch._consecutive_no_advance = 2
    ch._maybe_seek(90.0, [])           # lag 90 > 17 → 快进
    assert abs(ch.last_agreed_seconds - 77.0) < 1e-6   # 90 − maxSeekStep(13)


def test_no_word_tick_keeps_prev_hypothesis_for_recovery():
    """无词 tick 不覆盖 _prev_words:语音恢复那轮仍能拿上一个有效 hypothesis 做 LCP 收敛
    (对齐生产:无词路径在 prevAbsoluteWords 赋值前 return)。"""
    ch = _chan(anchor_words=1)
    good = _words([("我", 0, 1), ("们", 1, 2), ("去", 2, 3)])
    ch.ingest(good, buffer_end=3.0)            # 记住有效 hypothesis
    ch.ingest([], buffer_end=4.0)              # 无词 tick:不应清掉 prev
    # 恢复:同前缀再来 → 应能确认(若 prev 被清则首词无法确认)。
    out = ch.ingest(good + [_w("看", 3, 4)], buffer_end=5.0)
    # prev 未被清 → LCP=[我,们,去] 命中,确认前缀(去掉末 anchor)= 我们。若 prev 被清则 LCP=0、空确认。
    assert out and "我们" in "".join(c.text for c in out)


def test_meaningful_filter_excludes_punct_and_empty():
    assert _meaningful(_w("好", 0, 1)) is True
    assert _meaningful(_w("。", 0, 1)) is False
    assert _meaningful(_w("", 0, 1)) is False
    ch = _chan()
    ch.ingest([_w("。", 0, 1), _w("", 1, 2), _w("好", 2, 3)], buffer_end=3.0)
    # 仅「好」是实义词 → partial 只剩它
    assert ch.partial is not None and ch.partial.text == "好"


def test_flush_emits_trailing_tail_idempotent():
    ch = _chan()
    ch.ingest(_words([("最", 0, 1), ("后", 1, 2), ("一", 2, 3), ("句", 3, 4)]), buffer_end=4.0)
    out = ch.flush()
    assert any("最后一句" in c.text for c in out)
    assert all(c.confirmed for c in out)
    assert ch.last_agreed_seconds == 4.0      # 推到尾末
    assert ch.flush() == []                   # 幂等


def test_flush_drops_hallucination_tail():
    ch = _chan()
    ch.ingest(_words([("谢", 0, 1), ("谢", 1, 2), ("观", 2, 3), ("看", 3, 4)]), buffer_end=4.0)
    assert ch.flush() == []                   # 「谢谢观看」幻觉 → 不 emit
    assert ch.flush() == []


def test_clamp_to_base_jumps_cursor_and_clears_anchor():
    ch = _chan()
    ch.last_agreed_seconds = 5.0
    ch.last_agreed_words = _words([("旧", 5, 6)])
    ch.clamp_to_base(10.0)                    # 缓冲头滚过 5 → 跳到 10
    assert ch.last_agreed_seconds == 10.0
    assert ch.last_agreed_words == []
    ch.clamp_to_base(3.0)                     # 更早的 base → no-op
    assert ch.last_agreed_seconds == 10.0


def test_skip_to_and_mark_scanned_monotonic():
    ch = _chan()
    ch.skip_to(20.0)
    assert ch.last_agreed_seconds == 20.0 and ch.scanned_end == 20.0
    ch.skip_to(5.0)                           # 单调:不回退
    assert ch.last_agreed_seconds == 20.0 and ch.scanned_end == 20.0
    ch.mark_scanned(25.0)
    assert ch.scanned_end == 25.0
    ch.mark_scanned(10.0)
    assert ch.scanned_end == 25.0


def test_shift_words_to_absolute():
    shifted = WordChannel.shift_words(_words([("甲", 0.5, 1.0)]), 10.0)
    assert shifted[0].start == 10.5 and shifted[0].end == 11.0
    assert WordChannel.shift_words(_words([("乙", 1, 2)]), 0.0)[0].start == 1.0
