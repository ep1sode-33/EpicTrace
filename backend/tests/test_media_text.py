from pathlib import Path

from epictrace.media import get_processor
from epictrace.media.text import TextMediaProcessor


def test_text_processor_reads_markdown(tmp_path: Path):
    f = tmp_path / "note.md"
    f.write_text("# Title\nhello world", encoding="utf-8")
    proc = TextMediaProcessor()
    assert proc.supports(f) is True
    result = proc.process(f)
    assert "hello world" in result.text
    assert result.metadata["chars"] == len("# Title\nhello world")


def test_registry_returns_text_processor_for_txt(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    proc = get_processor(f)
    assert isinstance(proc, TextMediaProcessor)


def test_registry_returns_none_for_unknown(tmp_path: Path):
    # 图片/音频本期无 processor(pdf/docx/pptx 现已有 processor)
    assert get_processor(tmp_path / "a.png") is None
