from epictrace.asr.hallucination import HallucinationFilter


def test_drops_chinese_near_silence_hallucinations():
    f = HallucinationFilter()
    assert f.is_hallucination("谢谢观看")
    assert f.is_hallucination("请不吝点赞 订阅 转发")  # 子串命中
    assert not f.is_hallucination("虚拟内存的工作原理是")


def test_drops_english_near_silence():
    f = HallucinationFilter()
    assert f.is_hallucination("Thank you for watching")
    assert f.is_hallucination("you")
    assert not f.is_hallucination("the cache line is 64 bytes")


def test_recent_duplicate_detection():
    """FIX C:重叠时间窗内同文本(同段被重叠切片重转)判重;无关文本不判重。"""
    f = HallucinationFilter()
    # recent confirmed:(text, start, end)。候选段与首段时间重叠 → 重叠重转,判重。
    recent = [("这是一段测试", 0.0, 2.0), ("这是一段测试 内容", 2.0, 4.0)]
    assert f.is_duplicate("这是一段测试", 0.5, 2.5, recent)
    assert not f.is_duplicate("完全不同的句子", 0.5, 2.5, recent)


def test_repetition_loop_signature():
    f = HallucinationFilter()
    sig1 = f.signature("好 的 好 的")
    sig2 = f.signature("好的好的")
    # 归一化为词 token 序列后比对(中文按字/词)
    assert isinstance(sig1, str) and sig1 == f.signature("好 的  好 的")


def test_substring_of_repeated_words_not_duplicate():
    """STEP 5:正常重复词的子串包含不算重复——「测试测试」与「测试测试测试」是不同真实话语,
    短段只在「近乎完全相等」时才判重,绝不因子串包含误丢真实重复语音。"""
    f = HallucinationFilter()
    # recent 里只有「测试测试」(时间 0..2),新来「测试测试测试」(同窗重叠)是更长真实话语,
    # 不该被判重(子串关系不算重)。
    assert not f.is_duplicate("测试测试测试", 0.0, 2.0, [("测试测试", 0.0, 2.0)])
    # 反向同样:recent 有更长的,新来更短的也不该因子串被丢。
    assert not f.is_duplicate("测试测试", 0.0, 2.0, [("测试测试测试", 0.0, 2.0)])


def test_exact_repeat_overlapping_time_is_duplicate():
    """FIX C:完全相等且时间重叠(同一段被重叠窗口重转)→ 判重,避免重复落库。"""
    f = HallucinationFilter()
    assert f.is_duplicate("测试测试测试", 0.0, 2.0, [("测试测试测试", 0.0, 2.0)])
    assert f.is_duplicate("这是一段测试", 1.0, 3.0, [("别的", 5.0, 6.0), ("这是一段测试", 0.5, 2.5)])


def test_exact_repeat_non_overlapping_time_not_duplicate():
    """FIX C:同文本但时间明显错开(真实重复语音,如「测试」说三遍)→ 不判重,照常 emit。"""
    f = HallucinationFilter()
    # recent 是 0..2 的「测试」;候选是 10..12 的「测试」,时间不重叠 → 真实重复,不判重。
    assert not f.is_duplicate("测试", 10.0, 12.0, [("测试", 0.0, 2.0)])


def test_loop_suppression_requires_three_identical():
    """STEP 5:子串/loop 抑制只用于「连续 >=3 次同前缀 hypothesis」的退化生长循环。
    recent 末尾连续 >=2 段与本段成子串关系(加本段共 >=3)→ 抑制;不足则放行。
    退化循环是真幻觉,不依赖时间重叠(loop 段时间常连续递增)。"""
    f = HallucinationFilter()
    # 退化生长循环:recent 末尾连续两段都是本段子串(哈哈、哈哈哈),本段哈哈哈哈 → 共 >=3 → 抑制。
    assert f.is_duplicate("哈哈哈哈", 4.0, 6.0,
                          [("哈哈", 0.0, 2.0), ("哈哈哈", 2.0, 4.0)])
    # 只一段成子串关系(非连续 >=3 循环)→ 不抑制。
    assert not f.is_duplicate("哈哈哈哈哈", 4.0, 6.0, [("哈哈哈哈", 0.0, 2.0)])
