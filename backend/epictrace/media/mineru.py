from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Protocol

from epictrace.interfaces.media import MediaProcessor, MediaResult
from epictrace.media.errors import ExtractionEngineNotReady
from epictrace.media.mineru_runner import run_mineru


class _Provisioner(Protocol):
    def is_ready(self) -> bool: ...
    def mineru_bin(self) -> str: ...


# runner(pdf_path, out_dir, *, mineru_bin, model_source, timeout, effort, progress_cb) -> (markdown, content_list)
RunnerFn = Callable[..., tuple[str, list]]


def _page_count(content_list: list) -> int:
    pages = [b.get("page_idx") for b in content_list
             if isinstance(b, dict) and isinstance(b.get("page_idx"), int)]
    return (max(pages) + 1) if pages else 0


# MinerU 承接的富文档格式(pdf/docx/pptx;mineru 自动识别格式)。
_RICH_SUFFIXES = {".pdf", ".docx", ".pptx"}


class MinerUMediaProcessor(MediaProcessor):
    """富文档(pdf/docx/pptx)唯一引擎(无回退)。未就绪 → ExtractionEngineNotReady;
    子进程失败 → ExtractionFailed(由 runner 抛出,直接透传,不退回
    pypdf/python-docx/python-pptx)。"""

    def __init__(
        self,
        provisioner: _Provisioner,
        *,
        model_source: str,
        timeout: int,
        effort: str = "medium",
        runner: RunnerFn | None = None,
    ) -> None:
        self._provisioner = provisioner
        self._model_source = model_source
        self._timeout = timeout
        self._effort = effort
        self._runner = runner or run_mineru

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in _RICH_SUFFIXES

    def process(self, path: Path, *, progress_cb=None) -> MediaResult:
        if not self._provisioner.is_ready():
            raise ExtractionEngineNotReady(
                "高质量提取引擎尚未安装,请先在设置中安装 MinerU。"
            )
        with tempfile.TemporaryDirectory(prefix="mineru-") as tmp:
            markdown, content_list = self._runner(
                path, Path(tmp),
                mineru_bin=self._provisioner.mineru_bin(),
                model_source=self._model_source,
                timeout=self._timeout,
                effort=self._effort,
                progress_cb=progress_cb,
            )
        return MediaResult(
            text=markdown,
            metadata={
                "backend": "mineru-hybrid",
                "content_list": content_list,
                "pages": _page_count(content_list),
            },
        )
