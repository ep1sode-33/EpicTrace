from __future__ import annotations

from pathlib import Path

from epictrace.config import AppConfig
from epictrace.interfaces.media import MediaProcessor
from epictrace.media.text import TextMediaProcessor
from epictrace.media.mineru import MinerUMediaProcessor
from epictrace.media.mineru_provisioner import MinerUProvisioner

# 纯文本静态处理器(无需 config)。富文档(pdf/docx/pptx)三槽由 config 统一构造成
# 单个 MinerUMediaProcessor(见 _rich_processors)。
# 注:旧的 python 处理器 media/pdf.py、media/docx.py、media/pptx.py 三份文件保留但
# 不再注册(休眠,不在活动路径上)。
_STATIC_PROCESSORS: list[MediaProcessor] = [
    TextMediaProcessor(),
]


def _rich_processors(config: AppConfig) -> list[MediaProcessor]:
    provisioner = MinerUProvisioner(config.mineru_venv_dir)
    return [
        MinerUMediaProcessor(
            provisioner,
            model_source=getattr(config, "model_source", "modelscope"),
            timeout=getattr(config, "extraction_timeout", 600),
        )
    ]


def get_processor(path: Path, config: AppConfig) -> MediaProcessor | None:
    for proc in _STATIC_PROCESSORS:
        if proc.supports(path):
            return proc
    for proc in _rich_processors(config):
        if proc.supports(path):
            return proc
    return None
