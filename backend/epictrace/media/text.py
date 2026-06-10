from __future__ import annotations

from pathlib import Path

from epictrace.interfaces.media import MediaProcessor, MediaResult

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text"}


class TextMediaProcessor(MediaProcessor):
    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in TEXT_SUFFIXES

    def process(self, path: Path) -> MediaResult:
        text = path.read_text(encoding="utf-8", errors="replace")
        return MediaResult(text=text, metadata={"chars": len(text)})
