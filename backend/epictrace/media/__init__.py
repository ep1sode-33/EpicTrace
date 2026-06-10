from __future__ import annotations

from pathlib import Path

from epictrace.interfaces.media import MediaProcessor
from epictrace.media.text import TextMediaProcessor

# 注册表:以后加 pdf/docx/ppt/image processor 时只往这里追加(Plan 6)
_PROCESSORS: list[MediaProcessor] = [TextMediaProcessor()]


def get_processor(path: Path) -> MediaProcessor | None:
    for proc in _PROCESSORS:
        if proc.supports(path):
            return proc
    return None
