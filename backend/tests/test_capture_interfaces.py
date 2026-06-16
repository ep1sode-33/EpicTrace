def test_audio_source_protocol_importable():
    from epictrace.interfaces.audio import AudioSource  # noqa: F401


def test_transcriber_noop_returns_empty(tmp_path):
    from epictrace.interfaces.transcriber import NoopTranscriber
    out = NoopTranscriber().transcribe(str(tmp_path / "x.wav"))
    assert out == []
