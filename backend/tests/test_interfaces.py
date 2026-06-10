from epictrace.interfaces.segmenter import IdentitySegmenter, Segment
from epictrace.interfaces.media import MediaProcessor
from epictrace.interfaces.llm import LLMProvider
from epictrace.interfaces.embedding import EmbeddingProvider
from epictrace.interfaces.vector_store import VectorStore


def test_identity_segmenter_returns_single_segment():
    seg = IdentitySegmenter()
    events = [{"t": 0}, {"t": 1}, {"t": 2}]
    result = seg.segment(events, hint=None)
    assert len(result) == 1
    assert result[0].event_indices == [0, 1, 2]


def test_abcs_cannot_be_instantiated():
    for abc in (MediaProcessor, LLMProvider, EmbeddingProvider, VectorStore):
        try:
            abc()  # type: ignore[abstract]
            assert False, f"{abc.__name__} 不应可实例化"
        except TypeError:
            pass
