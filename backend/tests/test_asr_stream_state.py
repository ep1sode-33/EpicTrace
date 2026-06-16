from epictrace.asr.hallucination import HallucinationFilter
from epictrace.asr.stream_state import StreamState
from epictrace.asr.types import TranscriptSegment


def _seg(text, s, e):
    return TranscriptSegment(text=text, start=s, end=e, source="mic", words=[], confirmed=False)


def test_confirms_all_but_last_segment():
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    confirmed = st.ingest([_seg("第一句", 0, 2), _seg("第二句", 2, 4), _seg("第三句", 4, 6)])
    # 除最后一段外确认;partial=最后一段
    assert [c.text for c in confirmed] == ["第一句", "第二句"]
    assert st.partial.text == "第三句"
    assert st.last_confirmed_end == 4  # 推到第二段末


def test_hallucination_segment_dropped_but_not_seek_loss():
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    confirmed = st.ingest([_seg("谢谢观看", 0, 2), _seg("真实内容", 2, 4)])
    # 幻觉段被滤掉、不进 confirmed;但 last_confirmed_end 仍按真实进展推进(不丢音)
    assert all("谢谢观看" not in c.text for c in confirmed)


def test_force_confirm_after_no_progress():
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=2)
    # 单段反复(弱音不自然确认):连续无进展 N 轮后强制确认该段
    for _ in range(2):
        st.ingest([_seg("勉强这一句", 0, 2)])
    confirmed = st.ingest([_seg("勉强这一句", 0, 2)])
    assert any("勉强这一句" in c.text for c in confirmed)


def test_force_path_drops_hallucination_but_advances_cursor():
    """FIX E:force 强制确认路径也跑幻觉过滤;幻觉段不 emit,但游标仍推进(防卡死、不写垃圾)。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=2)
    for _ in range(2):
        st.ingest([_seg("谢谢观看", 0, 2)])
    confirmed = st.ingest([_seg("谢谢观看", 0, 2)])
    assert confirmed == []                      # 幻觉不落库
    assert st.last_confirmed_end == 2.0         # 但游标推进(stall 恢复)
    assert st.partial is None


def test_force_path_emits_real_text():
    """FIX E:force 路径遇真实文本则正常 emit。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=2)
    for _ in range(2):
        st.ingest([_seg("真实有效的一句话", 0, 2)])
    confirmed = st.ingest([_seg("真实有效的一句话", 0, 2)])
    assert any("真实有效的一句话" in c.text for c in confirmed)
    assert st.last_confirmed_end == 2.0


def test_flush_emits_real_pending_partial():
    """FIX 3:flush() 把当前真实 partial 走过滤门强制确认并 emit(收尾/IDLE 时不丢短尾)。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    # 单段 → 作 partial(未到 force 轮数,正常不会确认)。
    st.ingest([_seg("最后一句短话", 0, 1)])
    assert st.partial is not None
    emitted = st.flush()
    assert any("最后一句短话" in c.text for c in emitted)
    assert all(c.confirmed for c in emitted)
    assert st.partial is None
    assert st.last_confirmed_end == 1.0


def test_flush_drops_hallucination_partial():
    """FIX 3:flush() 的强制确认仍跑幻觉门;幻觉 partial 不 emit,但游标推进、partial 清空。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    st.ingest([_seg("谢谢观看", 0, 1)])
    assert st.partial is not None
    emitted = st.flush()
    assert emitted == []                  # 幻觉不落库
    assert st.partial is None
    assert st.last_confirmed_end == 1.0   # 游标仍推进(不丢音)


def test_flush_idempotent_no_partial():
    """FIX 3:无 partial 时 flush() emit 空;第二次 flush 也不重复 emit(幂等)。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    assert st.flush() == []
    st.ingest([_seg("一句话", 0, 1)])
    first = st.flush()
    assert any("一句话" in c.text for c in first)
    # 第二次:partial 已清空 → 不再 emit。
    assert st.flush() == []


def test_duplicate_confirmed_suppressed():
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    st.ingest([_seg("重复句", 0, 2), _seg("x", 2, 3)])
    again = st.ingest([_seg("重复句", 0, 2), _seg("y", 2, 3)])
    assert all("重复句" not in c.text for c in again)  # 最近 N 去重


def test_repeated_speech_emitted_once_not_dropped_or_duplicated():
    """STEP 5:用户连说「测试测试测试」(真实重复语音)应被 emit 一次,不丢、不重复。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    # 一窗:重复短语作首段(确认),后接尾段(partial)。
    confirmed = st.ingest([_seg("测试测试测试", 0, 2), _seg("尾", 2, 3)])
    assert [c.text for c in confirmed] == ["测试测试测试"]  # emit 一次
    # 下一窗:更长的真实重复「测试测试测试测试」是不同话语,子串不应误判重而被丢。
    again = st.ingest([_seg("测试测试测试测试", 3, 5), _seg("尾2", 5, 6)])
    assert any("测试测试测试测试" in c.text for c in again)


def test_dedup_rejection_does_not_advance_cursor():
    """STEP 5:真实语音被去重门拒(非幻觉)→ 不推进游标,以便该段音频可被重转(不永久丢)。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    # 先确认一段,使其进 recent;游标推到 2。
    st.ingest([_seg("重复句", 0, 2), _seg("x", 2, 3)])
    assert st.last_confirmed_end == 2.0
    cursor_before = st.last_confirmed_end
    # 同一签名再来(去重拒)作为多段窗的首段:不 emit,且游标不因这段推进。
    out = st.ingest([_seg("重复句", 10, 12), _seg("尾", 12, 13)])
    assert all("重复句" not in c.text for c in out)       # 去重不落库
    assert st.last_confirmed_end == cursor_before          # 去重拒:游标不推进(可重转)


def test_hallucination_rejection_still_advances_cursor():
    """STEP 5:近静音幻觉段被拒 → 仍推进游标(防 re-loop),区别于去重拒。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    out = st.ingest([_seg("谢谢观看", 0, 2), _seg("尾", 2, 3)])
    assert all("谢谢观看" not in c.text for c in out)   # 幻觉不落库
    assert st.last_confirmed_end == 2.0                  # 幻觉拒:游标仍推进(不 re-loop)
