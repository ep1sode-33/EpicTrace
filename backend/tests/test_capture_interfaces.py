def test_audio_source_protocol_importable():
    from epictrace.interfaces.audio import AudioSource  # noqa: F401


def test_transcriber_noop_returns_empty():
    from epictrace.interfaces.transcriber import NoopTranscriber
    out = NoopTranscriber().transcribe_window(
        b"", clip_start=0.0, prefix="", source="mic")
    assert out == []
