from __future__ import annotations

from pathlib import Path

from epictrace.interfaces.media import MediaProcessor
from epictrace.media.text import TextMediaProcessor
from epictrace.media.pdf import PdfMediaProcessor
from epictrace.media.docx import DocxMediaProcessor
from epictrace.media.pptx import PptxMediaProcessor

# 注册表:以后加 image/audio processor 时只往这里追加
_PROCESSORS: list[MediaProcessor] = [
    TextMediaProcessor(),
    PdfMediaProcessor(),
    DocxMediaProcessor(),
    PptxMediaProcessor(),
]


def get_processor(path: Path) -> MediaProcessor | None:
    for proc in _PROCESSORS:
        if proc.supports(path):
            return proc
    return None
