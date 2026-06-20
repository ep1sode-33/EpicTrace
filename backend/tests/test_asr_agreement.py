"""流式词级 prefix-agreement(LocalAgreement)纯逻辑测试。"""
from epictrace.asr.agreement import (
    AgreementState,
    longest_common_prefix,
    prefix_agreement,
)
from epictrace.asr.types import WordTiming


def _w(word, s, e):
    return WordTiming(word=word, start=s, end=e)


def _words(spec):
    # spec: [(word, start, end), ...]
    return [_w(*t) for t in spec]


def test_longest_common_prefix():
    a = _words([("我", 0, 1), ("们", 1, 2), ("去", 2, 3)])
    b = _words([("我", 0, 1), ("们", 1, 2), ("走", 2, 3)])
    lcp = longest_common_prefix(a, b)
    assert [w.word for w in lcp] == ["我", "们"]
    # 全不同 → 空;全同 → 全部。
    assert longest_common_prefix(a, _words([("你", 0, 1)])) == []
    assert [w.word for w in longest_common_prefix(a, a)] == ["我", "们", "去"]


def test_prefix_agreement_confirms_common_minus_anchor():
    prev = _words([("我", 0, 1), ("们", 1, 2), ("去", 2, 3), ("公", 3, 4)])
    cur = _words([("我", 0, 1), ("们", 1, 2), ("去", 2, 3), ("园", 3, 4)])
    # LCP=我们去(3),anchor=1 → confirmed=我们(2),anchor=去(1)。
    r = prefix_agreement(prev, cur, anchor_words=1)
    assert [w.word for w in r.confirmed] == ["我", "们"]
    assert [w.word for w in r.anchor] == ["去"]
    assert r.advanced is True


def test_prefix_agreement_first_round_no_confirm():
    cur = _words([("我", 0, 1), ("们", 1, 2)])
    assert prefix_agreement([], cur, anchor_words=1).advanced is False


def test_prefix_agreement_lcp_too_short():
    prev = _words([("我", 0, 1)])
    cur = _words([("我", 0, 1)])
    # LCP=1,anchor=1 → confirmed 空(不足以确认)。
    r = prefix_agreement(prev, cur, anchor_words=1)
    assert r.confirmed == [] and r.advanced is False


def test_agreement_state_streaming_confirms_stable_prefix():
    st = AgreementState(anchor_words=1)
    # 第一轮:无 prev → 不确认,记 prev。
    assert st.ingest(_words([("我", 0, 1), ("们", 1, 2)])) == []
    # 第二轮:LCP=我们,confirmed=我(anchor=们)。
    out = st.ingest(_words([("我", 0, 1), ("们", 1, 2), ("去", 2, 3)]))
    assert [w.word for w in out] == ["我"]
    assert st.confirmed_end == 1.0 and st.anchor_text() == "们"


def test_agreement_state_force_confirm_after_no_progress():
    """连续 force_confirm_after 轮无进展(每轮 hypothesis 都对不上前缀)→ 强制确认防卡死。"""
    st = AgreementState(anchor_words=1, force_confirm_after=2)
    st.ingest(_words([("甲", 0, 1), ("乙", 1, 2)]))         # 首轮:记 prev
    # 之后每轮首词都变(LCP=0,无进展)。
    st.ingest(_words([("丙", 0, 1), ("丁", 1, 2)]))         # no_advance=1
    out = st.ingest(_words([("戊", 0, 1), ("己", 1, 2)]))   # no_advance=2 → 强制确认除末 anchor 外
    assert [w.word for w in out] == ["戊"]                   # 强制确认首词,anchor=己
