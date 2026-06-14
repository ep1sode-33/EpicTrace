import pytest

from epictrace.media.errors import ExtractionEngineNotReady, ExtractionFailed


def test_exceptions_are_exceptions_with_message():
    assert issubclass(ExtractionEngineNotReady, Exception)
    assert issubclass(ExtractionFailed, Exception)
    assert str(ExtractionEngineNotReady("not ready")) == "not ready"
    assert str(ExtractionFailed("boom")) == "boom"


def test_exceptions_are_distinct():
    with pytest.raises(ExtractionEngineNotReady):
        raise ExtractionEngineNotReady("x")
    with pytest.raises(ExtractionFailed):
        raise ExtractionFailed("y")
    assert ExtractionEngineNotReady is not ExtractionFailed
