from epictrace.asr.types import TranscriptSegment, WordTiming


def test_segment_holds_words_source_confirmed():
    seg = TranscriptSegment(
        text="你好世界", start=1.0, end=2.5, source="mic",
        words=[WordTiming(word="你好", start=1.0, end=1.6),
               WordTiming(word="世界", start=1.6, end=2.5)],
        confirmed=True,
    )
    assert seg.source == "mic" and seg.confirmed is True
    assert [w.word for w in seg.words] == ["你好", "世界"]
