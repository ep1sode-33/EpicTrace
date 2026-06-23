from epictrace.indexing.chunker import Chunk, chunk_text


def test_empty_text_yields_no_chunks():
    assert chunk_text("") == []


def test_whitespace_only_text_yields_no_chunks():
    assert chunk_text("   \n\t  \n\n") == []


def test_short_text_single_chunk_exact_offsets():
    t = "hello world"
    chunks = chunk_text(t, target=1800, overlap=200)
    assert len(chunks) == 1
    c = chunks[0]
    assert isinstance(c, Chunk)
    assert (c.char_start, c.char_end) == (0, len(t))
    assert t[c.char_start:c.char_end] == c.text


def test_offsets_always_match_source_substring():
    t = ("段落一。" * 200) + "\n\n" + ("paragraph two. " * 200)
    chunks = chunk_text(t, target=400, overlap=50)
    assert len(chunks) > 1
    for c in chunks:
        assert t[c.char_start:c.char_end] == c.text   # 偏移必须对得上原文
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(t)               # 覆盖到结尾


def test_consecutive_chunks_overlap_and_progress():
    t = "x" * 2000
    chunks = chunk_text(t, target=500, overlap=100)
    for a, b in zip(chunks, chunks[1:]):
        assert b.char_start < a.char_end      # 有重叠
        assert b.char_start > a.char_start    # 一直前进,不死循环


def test_chars_per_token_by_language():
    from epictrace.indexing.chunker import _chars_per_token
    assert 3.5 <= _chars_per_token("the quick brown fox jumps " * 20) <= 4.0   # 英文 ~4
    assert 1.2 <= _chars_per_token("这是一段用于测试切块的中文文本资料。" * 20) <= 1.5  # 中文 ~1.3


def test_chinese_chunks_scaled_down_to_token_budget():
    # 中文:1800 字默认目标应被缩放(~585 字),块远小于英文 1800;偏移/覆盖不变量保持。
    zh = "这是一段用于测试切块标定的中文文本资料。" * 300   # ~6000 中文字符
    chunks = chunk_text(zh)
    assert len(chunks) >= 1
    biggest = max(len(c.text) for c in chunks)
    assert biggest < 1000, f"中文块仍过大: {biggest}"          # 缩放生效(否则 ~1800)
    for c in chunks:
        assert zh[c.char_start:c.char_end] == c.text          # 偏移对齐
    assert chunks[0].char_start == 0 and chunks[-1].char_end == len(zh)  # 全覆盖


def test_english_target_unchanged():
    # 纯英文:缩放比≈1,块仍接近 1800(向后兼容,不回归英文行为)。
    en = "word " * 1000   # 5000 英文字符
    biggest = max(len(c.text) for c in chunk_text(en))
    assert 1500 <= biggest <= 1900
