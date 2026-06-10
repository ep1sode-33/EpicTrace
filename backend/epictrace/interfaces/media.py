from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MediaResult:
    text: str
    metadata: dict = field(default_factory=dict)


class MediaProcessor(ABC):
    @abstractmethod
    def supports(self, path: Path) -> bool: ...

    @abstractmethod
    def process(self, path: Path) -> MediaResult: ...
