from __future__ import annotations

from pathlib import Path

from epictrace.config import AppConfig
from epictrace.interfaces.media import MediaProcessor
from epictrace.media.docx import DocxMediaProcessor
from epictrace.media.mineru import MinerUMediaProcessor
from epictrace.media.mineru_provisioner import MinerUProvisioner
from epictrace.media.pdf import PdfMediaProcessor
from epictrace.media.pptx import PptxMediaProcessor
from epictrace.media.text import TextMediaProcessor

# 纯文本静态处理器(无需 config)。text/code/data 始终走 TextMediaProcessor,与引擎无关。
_STATIC_PROCESSORS: list[MediaProcessor] = [
    TextMediaProcessor(),
]

# 富文档(pdf/docx/pptx)的内置 pypdf 引擎处理器(免安装、免模型)。无状态,可静态实例化。
_PYPDF_PROCESSORS: list[MediaProcessor] = [
    PdfMediaProcessor(),
    DocxMediaProcessor(),
    PptxMediaProcessor(),
]


def _rich_processors(config: AppConfig) -> list[MediaProcessor]:
    """据持久化的 extraction.engine 决定富文档(pdf/docx/pptx)处理器。

    - engine=="pypdf"(默认)→ 内置 pypdf/python-docx/python-pptx 处理器(开箱即用,不碰 MinerU)。
    - engine=="mineru" → 单个 MinerUMediaProcessor(OCR/VLM,质量高;需安装+下模型)。
    """
    # 引入放函数内,避免顶层 import 形成 settings ↔ media 循环依赖。
    from epictrace.services.settings import SettingsService

    ext = SettingsService(config).get_extraction_settings()
    if ext["engine"] == "mineru":
        provisioner = MinerUProvisioner(config.mineru_venv_dir)
        return [
            MinerUMediaProcessor(
                provisioner,
                model_source=ext["model_source"],
                timeout=getattr(config, "extraction_timeout", 600),
                effort=ext["effort"],
            )
        ]
    # 默认 / pypdf:内置处理器(engine=pypdf 时完全不构造 MinerU/provisioner)。
    return _PYPDF_PROCESSORS


def get_processor(path: Path, config: AppConfig) -> MediaProcessor | None:
    for proc in _STATIC_PROCESSORS:
        if proc.supports(path):
            return proc
    for proc in _rich_processors(config):
        if proc.supports(path):
            return proc
    return None
