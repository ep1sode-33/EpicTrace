from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AudioSource(Protocol):
    """采音接口缝(延后实现)。覆盖麦克风(采集)与系统声音(采集)两类来源——
    后续各落一个实现(mic plan / 系统声音采集 plan,见
    docs/reference/asr-streaming-tuning-notes.md §5)。本期不实现。"""

    def start(self, session_id: int) -> None: ...

    def stop(self) -> list[str]:
        """停止采集,返回落盘的音频文件绝对路径列表。"""
        ...
