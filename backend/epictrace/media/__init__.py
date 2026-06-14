from __future__ import annotations

from pathlib import Path

from epictrace.config import AppConfig
from epictrace.interfaces.media import MediaProcessor
from epictrace.media.text import TextMediaProcessor
from epictrace.media.pdf import PdfMediaProcessor
from epictrace.media.docx import DocxMediaProcessor
from epictrace.media.pptx import PptxMediaProcessor

# 纯文本静态处理器(无需 config)。富文档(pdf/docx/pptx)三槽由 config 构造
# (见 _rich_processors)。
_STATIC_PROCESSORS: list[MediaProcessor] = [
    TextMediaProcessor(),
]


def _rich_processors(config: AppConfig) -> list[MediaProcessor]:
    # Task 6 起统一改为从 config 构造单个 MinerUMediaProcessor(同时承接
    # pdf/docx/pptx);暂时仍用各自的 python 处理器以保持套件绿。
    return [
        PdfMediaProcessor(),
        DocxMediaProcessor(),
        PptxMediaProcessor(),
    ]


def get_processor(path: Path, config: AppConfig) -> MediaProcessor | None:
    for proc in _STATIC_PROCESSORS:
        if proc.supports(path):
            return proc
    for proc in _rich_processors(config):
        if proc.supports(path):
            return proc
    return None
