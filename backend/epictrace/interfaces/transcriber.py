from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start: float  # 秒
    end: float


@runtime_checkable
class Transcriber(Protocol):
    """ASR 接口缝(延后实现)。mic ASR plan 落 faster-whisper 实现
    (调参/幻觉过滤见 docs/reference/asr-streaming-tuning-notes.md)。"""

    def transcribe(self, audio_path: str) -> list[TranscriptSegment]: ...


class NoopTranscriber:
    """本期默认:不转写,返回空。"""

    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        return []
