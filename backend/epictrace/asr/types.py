from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WordTiming:
    word: str
    start: float  # 秒
    end: float


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start: float
    end: float
    source: str  # "mic" | "device"
    words: list[WordTiming] = field(default_factory=list)
    confirmed: bool = False  # True=已确认(落库),False=partial(实时显示)
