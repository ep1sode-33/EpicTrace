from epictrace.interfaces.embedding import EmbeddingProvider


class FakeEmbedder(EmbeddingProvider):
    """确定性 1024 维向量,遵守 EmbeddingProvider 契约;不依赖 torch。"""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = (sum(ord(c) for c in t) % 97) / 97.0
            out.append([h] * self._dim)
        return out

    @property
    def model_id(self) -> str:
        return "fake"
