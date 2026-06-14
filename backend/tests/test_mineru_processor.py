from pathlib import Path

import pytest

from epictrace.media.errors import ExtractionEngineNotReady, ExtractionFailed
from epictrace.media.mineru import MinerUMediaProcessor


class _FakeProvisioner:
    def __init__(self, ready: bool):
        self._ready = ready

    def is_ready(self) -> bool:
        return self._ready

    def mineru_bin(self) -> str:
        return "/venv/bin/mineru"


def test_supports_rich_documents_always(tmp_path: Path):
    proc = MinerUMediaProcessor(_FakeProvisioner(ready=False),
                                model_source="modelscope", timeout=600)
    assert proc.supports(Path("x.pdf")) is True
    assert proc.supports(Path("X.PDF")) is True
    assert proc.supports(Path("x.docx")) is True
    assert proc.supports(Path("x.DOCX")) is True
    assert proc.supports(Path("x.pptx")) is True
    assert proc.supports(Path("x.PPTX")) is True
    assert proc.supports(Path("x.txt")) is False
    assert proc.supports(Path("x.png")) is False


def test_not_ready_raises_engine_not_ready(tmp_path: Path):
    proc = MinerUMediaProcessor(_FakeProvisioner(ready=False),
                                model_source="modelscope", timeout=600)
    with pytest.raises(ExtractionEngineNotReady):
        proc.process(tmp_path / "x.pdf")


def test_ready_returns_media_result(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"; pdf.write_bytes(b"%PDF")
    content = [{"type": "text", "text": "hi", "page_idx": 0},
               {"type": "text", "text": "bye", "page_idx": 1}]
    captured = {}

    def fake_runner(pdf_path, out_dir, *, mineru_bin, model_source, timeout, effort, progress_cb=None, cancel=None):
        captured["mineru_bin"] = mineru_bin
        captured["model_source"] = model_source
        captured["timeout"] = timeout
        captured["effort"] = effort
        return "# Title\n\nbody", content

    proc = MinerUMediaProcessor(_FakeProvisioner(ready=True),
                                model_source="modelscope", timeout=600,
                                runner=fake_runner)
    result = proc.process(pdf)
    assert result.text == "# Title\n\nbody"
    assert result.metadata["backend"] == "mineru-hybrid"
    assert result.metadata["content_list"] == content
    assert result.metadata["pages"] == 2  # max page_idx + 1
    assert captured["mineru_bin"] == "/venv/bin/mineru"
    assert captured["model_source"] == "modelscope"
    assert captured["timeout"] == 600
    assert captured["effort"] == "medium"  # processor default effort


def test_effort_forwarded_to_runner(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"; pdf.write_bytes(b"%PDF")
    captured = {}

    def fake_runner(pdf_path, out_dir, *, mineru_bin, model_source, timeout, effort, progress_cb=None, cancel=None):
        captured["effort"] = effort
        return "# ok\n\nbody", []

    proc = MinerUMediaProcessor(_FakeProvisioner(ready=True),
                                model_source="modelscope", timeout=600,
                                effort="high", runner=fake_runner)
    proc.process(pdf)
    assert captured["effort"] == "high"


def test_process_forwards_progress_cb_to_runner(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"; pdf.write_bytes(b"%PDF")
    captured = {}

    def fake_runner(pdf_path, out_dir, *, mineru_bin, model_source, timeout, effort, progress_cb=None, cancel=None):
        captured["progress_cb"] = progress_cb
        if progress_cb is not None:
            progress_cb("解析中 1/2")
        return "# ok\n\nbody", []

    proc = MinerUMediaProcessor(_FakeProvisioner(ready=True),
                                model_source="modelscope", timeout=600,
                                runner=fake_runner)
    seen: list[str] = []
    proc.process(pdf, progress_cb=seen.append)
    assert captured["progress_cb"] is not None
    assert seen == ["解析中 1/2"]


def test_process_forwards_cancel_to_runner(tmp_path: Path):
    # FIX 2:cancel 事件须从 process(...) 透传给 runner(供 SSE 断开时停掉 mineru)。
    import threading
    pdf = tmp_path / "paper.pdf"; pdf.write_bytes(b"%PDF")
    captured = {}
    ev = threading.Event()

    def fake_runner(pdf_path, out_dir, *, mineru_bin, model_source, timeout, effort, progress_cb=None, cancel=None):
        captured["cancel"] = cancel
        return "# ok\n\nbody", []

    proc = MinerUMediaProcessor(_FakeProvisioner(ready=True),
                                model_source="modelscope", timeout=600,
                                runner=fake_runner)
    proc.process(pdf, cancel=ev)
    assert captured["cancel"] is ev


def test_runner_failure_propagates_as_extraction_failed(tmp_path: Path):
    pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")

    def boom(pdf_path, out_dir, *, mineru_bin, model_source, timeout, effort, progress_cb=None, cancel=None):
        raise ExtractionFailed("subprocess died")

    proc = MinerUMediaProcessor(_FakeProvisioner(ready=True),
                                model_source="modelscope", timeout=600,
                                runner=boom)
    with pytest.raises(ExtractionFailed):
        proc.process(pdf)
