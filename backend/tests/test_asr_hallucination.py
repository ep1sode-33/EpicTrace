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
    f = HallucinationFilter()
    recent = ["这是一段测试", "这是一段测试 内容"]
    assert f.is_duplicate("这是一段测试", recent)
    assert not f.is_duplicate("完全不同的句子", recent)


def test_repetition_loop_signature():
    f = HallucinationFilter()
    sig1 = f.signature("好 的 好 的")
    sig2 = f.signature("好的好的")
    # 归一化为词 token 序列后比对(中文按字/词)
    assert isinstance(sig1, str) and sig1 == f.signature("好 的  好 的")
