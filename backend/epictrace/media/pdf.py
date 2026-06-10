from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from epictrace.interfaces.media import MediaProcessor, MediaResult


class PdfMediaProcessor(MediaProcessor):
    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def process(self, path: Path) -> MediaResult:
        reader = PdfReader(str(path))
        parts = [(page.extract_text() or "") for page in reader.pages]
        text = "\n\n".join(parts)
        return MediaResult(text=text, metadata={"pages": len(reader.pages)})
