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


def test_intra_segment_loop_detection():
    """rank3:段内立即重复(>=4 字长串多遍无间隔拼接)判幻觉;短单元真实重复语音绝不误杀。"""
    f = HallucinationFilter()
    # 正例:长串两遍 / 三遍。
    assert f.is_intra_segment_loop("我会去看你我会去看你")
    assert f.is_intra_segment_loop("这次没有这次没有")
    assert f.is_hallucination("我会去看你 我会去看你")  # 经 is_hallucination 命中(空白被签名归一)
    # 反例:短重复单元(<4 字)一律放行,保住真实重复语音。
    assert not f.is_intra_segment_loop("测试测试测试")
    assert not f.is_intra_segment_loop("你好你好你好今天天气很好")
    assert not f.is_intra_segment_loop("哈哈哈哈")
    assert not f.is_intra_segment_loop("好的好的")
    assert not f.is_hallucination("你好你好你好今天天气很好")


def test_expanded_silence_patterns_and_substring_consistency():
    """rank2:扩充的静音/标注精确串 + 子串对 clean+lower 后文本一致比对;正常句不误伤。"""
    f = HallucinationFilter()
    assert f.is_hallucination("谢谢大家")        # 上游 t2s 后的简体
    assert f.is_hallucination("我们下期再见")
    assert f.is_hallucination("[music]")
    assert f.is_hallucination("一键三连支持一下")  # 子串命中
    assert not f.is_hallucination("我们下周再讨论这个方案")  # 正常句不误伤
    assert not f.is_hallucination("关注我们团队的进展")      # 收紧后不再误伤


def test_repetition_loop_catches_degenerate_single_char_runs():
    """STEP(集成验证发现):弱音尾巴退化复读环——单字超长连串 / 分词后高重复 → 判幻觉,
    但短重复语音放行(不误杀)。"""
    f = HallucinationFilter()
    # 单字超长连串(无空格)→ char streak >=8。
    assert f.is_repetition_loop("是" * 30)
    assert f.is_hallucination("是" * 40)
    # 空格分词后高重复(唯一率<0.4)。
    assert f.is_repetition_loop(" ".join(["是"] * 30))
    assert f.is_repetition_loop("unden" * 9)            # 子词重复(无空格)长连串
    # 反例:正常重复/短串放行。
    assert not f.is_repetition_loop("测试测试测试测试")   # 交替双字,无单字长连串
    assert not f.is_repetition_loop("对对对")             # 太短
    assert not f.is_repetition_loop("哈哈哈哈")           # 4 连 <8
    assert not f.is_repetition_loop("大家好呀最近有没有被这个消息刷屏")  # 正常句
    assert not f.is_hallucination("大家好呀最近有没有被这个消息刷屏")


def test_silence_watermark_hallucinations_filtered():
    """真机实测:权限被拒/弱音输入时 Whisper 脑补的平台水印(静音幻觉)→ 整段或子串命中即滤。"""
    f = HallucinationFilter()
    assert f.is_hallucination("优优独播剧场YoYo Television Series Exclusive")
    assert f.is_hallucination("优优独播剧场")
    assert f.is_hallucination("中文字幕志愿者")
    assert not f.is_hallucination("我们来聊聊独播剧场的运营")  # 正常句不误伤(无完整水印串)


def test_is_low_quality_segment_drops_decode_garbage():
    """段级质量过滤(isLowQualitySegment 移植):极低 avg_logprob / 极高 compression_ratio 的解码退化段
    被判低质丢弃;正常段放行。"""
    f = HallucinationFilter()
    # 正常段(高置信、低压缩比)→ 放行。
    assert not f.is_low_quality("大家好今天聊聊摩托车赛车服", avg_logprob=-0.2, compression_ratio=1.1)
    # avg_logprob 极低 → 低质。
    assert f.is_low_quality("含糊不清的一段", avg_logprob=-2.5, compression_ratio=1.2)
    # 低 logprob + 极短 → 低质。
    assert f.is_low_quality("嗯啊", avg_logprob=-1.6, compression_ratio=1.0)
    # 高压缩比 + 够长 → 解码复读环 → 低质。
    assert f.is_low_quality("这个这个这个这个这个这个", avg_logprob=-0.5, compression_ratio=3.5)
    # 关闭过滤器 → 一律放行。
    assert not HallucinationFilter(enabled=False).is_low_quality("x", -9.0, 9.0)
