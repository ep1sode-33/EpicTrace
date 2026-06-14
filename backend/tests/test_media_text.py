from pathlib import Path

from epictrace.config import AppConfig
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
    proc = get_processor(f, AppConfig(data_dir=tmp_path))
    assert isinstance(proc, TextMediaProcessor)


def test_text_processor_covers_code_and_data_suffixes(tmp_path: Path):
    # 代码/数据类纯文本现在也走 TextMediaProcessor:与 scan 白名单对齐,
    # 不再出现「扫描登记了、却没有 processor 永远卡住」的文件。
    for name, body in [("mod.py", "print('hi')\n"), ("data.json", '{"k": 1}')]:
        f = tmp_path / name
        f.write_text(body, encoding="utf-8")
        proc = get_processor(f, AppConfig(data_dir=tmp_path))
        assert isinstance(proc, TextMediaProcessor)
        assert body in proc.process(f).text


def test_registry_returns_none_for_unknown(tmp_path: Path):
    # 图片/音频本期无 processor(pdf/docx/pptx 现已有 processor)
    assert get_processor(tmp_path / "a.png", AppConfig(data_dir=tmp_path)) is None
