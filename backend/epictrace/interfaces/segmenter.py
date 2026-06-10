from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    event_indices: list[int]
    project_hint: str | None = None


class Segmenter(ABC):
    @abstractmethod
    def segment(self, events: list[dict], hint: str | None) -> list[Segment]: ...


class IdentitySegmenter(Segmenter):
    """默认:整段 = 1 段。以后换 LLM 切割时只替换本类。"""

    def segment(self, events: list[dict], hint: str | None) -> list[Segment]:
        return [Segment(event_indices=list(range(len(events))), project_hint=hint)]
