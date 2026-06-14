from pathlib import Path

import pytest

from epictrace.config import AppConfig
from epictrace.media import get_processor
from epictrace.media.errors import ExtractionEngineNotReady
from epictrace.media.mineru import MinerUMediaProcessor


@pytest.mark.parametrize("name", ["a.pdf", "a.docx", "a.pptx"])
def test_rich_doc_slots_are_mineru_not_python_processors(tmp_path: Path, name: str):
    # 富文档(pdf/docx/pptx)统一走 MinerU;pypdf/python-docx/python-pptx 不再被选中。
    p = tmp_path / name
    p.write_bytes(b"x")  # 只验证选路 + 无回退;不需真实文件内容
    proc = get_processor(p, AppConfig(data_dir=tmp_path))
    assert isinstance(proc, MinerUMediaProcessor)
    # 未 provision → 处理报错(无回退,不返回 python 处理器文本)
    with pytest.raises(ExtractionEngineNotReady):
        proc.process(p)


def test_unknown_type_returns_none(tmp_path: Path):
    assert get_processor(tmp_path / "a.png", AppConfig(data_dir=tmp_path)) is None    # 图片本期无 processor
    assert get_processor(tmp_path / "a.mp3", AppConfig(data_dir=tmp_path)) is None     # 音频本期无 processor
