from __future__ import annotations

from pathlib import Path

from epictrace.interfaces.media import MediaProcessor, MediaResult

# 所有可按 UTF-8 直接读出的纯文本/代码/数据后缀。
# 与 scan 的 INDEXABLE_SUFFIXES 对齐(扣掉走专用 processor 的 pdf/docx/pptx),
# 避免出现「扫描登记了、却没有 processor 永远卡住」的文件。
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".text", ".rst",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".json", ".yaml", ".yml", ".toml", ".csv", ".html", ".css", ".sql",
}


class TextMediaProcessor(MediaProcessor):
    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in TEXT_SUFFIXES

    def process(self, path: Path, *, progress_cb=None) -> MediaResult:
        text = path.read_text(encoding="utf-8", errors="replace")
        return MediaResult(text=text, metadata={"chars": len(text)})
