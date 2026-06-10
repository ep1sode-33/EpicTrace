from __future__ import annotations

from abc import ABC, abstractmethod


class VectorStore(ABC):
    """实现留给后续 Plan(MilvusLiteStore)。"""

    @abstractmethod
    def upsert(self, records: list[dict]) -> None: ...

    @abstractmethod
    def query(self, vector: list[float], filter: dict | None, k: int) -> list[dict]: ...
