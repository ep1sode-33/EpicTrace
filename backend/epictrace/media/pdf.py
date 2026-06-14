from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

from epictrace.interfaces.media import MediaProcessor, MediaResult

# pypdf 对轻微不规范的 PDF 会喷 "Ignoring wrong pointing object …" 之类 WARNING;
# 它能自行恢复,这些噪声不该污染应用输出。抬到 ERROR 即静音(真错误仍会显示)。
logging.getLogger("pypdf").setLevel(logging.ERROR)


class PdfMediaProcessor(MediaProcessor):
    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def process(self, path: Path, *, progress_cb=None, cancel=None) -> MediaResult:
        reader = PdfReader(str(path))
        parts = [(page.extract_text() or "") for page in reader.pages]
        text = "\n\n".join(parts)
        return MediaResult(text=text, metadata={"pages": len(reader.pages)})
