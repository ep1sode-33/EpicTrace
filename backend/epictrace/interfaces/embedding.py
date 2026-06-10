from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """实现留给后续 Plan(BGE-M3 本地等)。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def model_id(self) -> str: ...
