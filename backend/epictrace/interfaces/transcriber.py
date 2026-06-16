from __future__ import annotations

from typing import Protocol, runtime_checkable

from epictrace.asr.types import TranscriptSegment


@runtime_checkable
class Transcriber(Protocol):
    """流式 ASR 引擎封装:对一个滚动窗口的 PCM 做一次转写。
    流式循环(StreamState/交替/确认)在 asr.worker,不在这里。"""

    def transcribe_window(
        self, pcm, *, clip_start: float, prefix: str, source: str, language: str = "zh"
    ) -> list[TranscriptSegment]: ...


class NoopTranscriber:
    """占位:不转写(Plan 8 留的默认,保留给无引擎场景)。"""

    def transcribe_window(self, pcm, *, clip_start, prefix, source, language="zh"):
        return []
