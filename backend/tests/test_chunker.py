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
