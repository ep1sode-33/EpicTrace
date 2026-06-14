from pathlib import Path

from epictrace.config import AppConfig
from epictrace.media import get_processor


def test_docx_extraction(tmp_path: Path):
    from docx import Document
    p = tmp_path / "a.docx"
    doc = Document(); doc.add_paragraph("虚拟内存"); doc.add_paragraph("page table"); doc.save(p)
    proc = get_processor(p, AppConfig(data_dir=tmp_path))
    assert proc is not None
    text = proc.process(p).text
    assert "虚拟内存" in text and "page table" in text


def test_pptx_extraction(tmp_path: Path):
    from pptx import Presentation
    p = tmp_path / "a.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Slide One"
    prs.save(p)
    proc = get_processor(p, AppConfig(data_dir=tmp_path))
    assert proc is not None
    assert "Slide One" in proc.process(p).text


def test_pdf_extraction(tmp_path: Path):
    from reportlab.pdfgen import canvas
    p = tmp_path / "a.pdf"
    c = canvas.Canvas(str(p)); c.drawString(72, 720, "Hello PDF world"); c.save()
    proc = get_processor(p, AppConfig(data_dir=tmp_path))
    assert proc is not None
    assert "Hello PDF" in proc.process(p).text


def test_unknown_type_returns_none(tmp_path: Path):
    assert get_processor(tmp_path / "a.png", AppConfig(data_dir=tmp_path)) is None    # 图片本期无 processor
    assert get_processor(tmp_path / "a.mp3", AppConfig(data_dir=tmp_path)) is None     # 音频本期无 processor
