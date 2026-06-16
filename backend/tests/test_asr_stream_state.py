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


def test_duplicate_confirmed_suppressed_when_time_overlaps():
    """FIX C:同段被重叠窗口重转(同文本、时间重叠)→ 去重压住,不重复落库。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    st.ingest([_seg("重复句", 0, 2), _seg("x", 2, 3)])
    # 时间重叠的同文本(0.5..2.5 与已确认 0..2 相交)= 重叠重转 → 去重。
    again = st.ingest([_seg("重复句", 0.5, 2.5), _seg("y", 2.5, 3.5)])
    assert all("重复句" not in c.text for c in again)  # 重叠重转被去重


def test_repeated_speech_time_separated_all_emit():
    """FIX C:用户把「测试」说三遍(同文本、时间明显错开)= 真实重复语音,三段全 emit,
    都不被去重门丢弃、也不卡住游标。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    texts = []
    # 三段时间不重叠:0..2 / 5..7 / 10..12;各作多段窗首段被确认。
    for (s, e), tail in [((0, 2), (2, 3)), ((5, 7), (7, 8)), ((10, 12), (12, 13))]:
        out = st.ingest([_seg("测试", s, e), _seg("尾", *tail)])
        texts += [c.text for c in out if "测试" in c.text]
    assert texts == ["测试", "测试", "测试"]  # 三遍全 emit,无一被丢/卡
    assert st.last_confirmed_end == 12.0      # 游标推进到第三段末(未卡住)


def test_repeated_speech_emitted_once_not_dropped_or_duplicated():
    """STEP 5:更长的真实重复「测试测试测试测试」是不同话语,子串不应误判重而被丢。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    confirmed = st.ingest([_seg("测试测试测试", 0, 2), _seg("尾", 2, 3)])
    assert [c.text for c in confirmed] == ["测试测试测试"]  # emit 一次
    again = st.ingest([_seg("测试测试测试测试", 3, 5), _seg("尾2", 5, 6)])
    assert any("测试测试测试测试" in c.text for c in again)


def test_dedup_rejection_does_not_advance_cursor():
    """STEP 5 + FIX C:同段被重叠窗口重转(去重拒,非幻觉)→ 不推进游标,以便该段音频
    可被重转(不永久丢)。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    # 先确认一段,使其进 recent;游标推到 2。
    st.ingest([_seg("重复句", 0, 2), _seg("x", 2, 3)])
    assert st.last_confirmed_end == 2.0
    cursor_before = st.last_confirmed_end
    # 时间重叠的同签名再来(去重拒)作为多段窗的首段:不 emit,且游标不因这段推进。
    out = st.ingest([_seg("重复句", 0.5, 2.5), _seg("尾", 2.5, 3.5)])
    assert all("重复句" not in c.text for c in out)       # 去重不落库
    assert st.last_confirmed_end == cursor_before          # 去重拒:游标不推进(可重转)


def test_loop_hallucination_repetition_still_suppressed():
    """FIX C:退化生长循环(连续 >=3 次同前缀 hypothesis)即便时间递增也仍被抑制(真幻觉)。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    # 三段连续子串生长(哈哈 / 哈哈哈 / 哈哈哈哈),时间连续递增。前两段会被确认进 recent,
    # 第三段触发 loop 抑制(末尾连续 >=2 段成子串关系)。
    st.ingest([_seg("哈哈", 0, 2), _seg("x", 2, 3)])
    st.ingest([_seg("哈哈哈", 3, 5), _seg("y", 5, 6)])
    out = st.ingest([_seg("哈哈哈哈", 6, 8), _seg("z", 8, 9)])
    assert all("哈哈哈哈" not in c.text for c in out)  # 退化循环被抑制


def test_hallucination_rejection_still_advances_cursor():
    """STEP 5:近静音幻觉段被拒 → 仍推进游标(防 re-loop),区别于去重拒。"""
    st = StreamState(source="mic", filter=HallucinationFilter(), force_confirm_after=4)
    out = st.ingest([_seg("谢谢观看", 0, 2), _seg("尾", 2, 3)])
    assert all("谢谢观看" not in c.text for c in out)   # 幻觉不落库
    assert st.last_confirmed_end == 2.0                  # 幻觉拒:游标仍推进(不 re-loop)
