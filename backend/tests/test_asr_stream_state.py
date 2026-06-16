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


def test_duplicate_confirmed_suppressed():
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    st.ingest([_seg("重复句", 0, 2), _seg("x", 2, 3)])
    again = st.ingest([_seg("重复句", 0, 2), _seg("y", 2, 3)])
    assert all("重复句" not in c.text for c in again)  # 最近 N 去重
