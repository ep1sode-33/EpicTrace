import json
import subprocess
from pathlib import Path

import pytest

from epictrace.media.errors import ExtractionFailed
from epictrace.media.mineru_runner import run_mineru


def _fake_ok(out_dir: Path, stem: str, markdown: str, content_list: list):
    """Return a runner that, when invoked, writes mineru's expected output tree
    (<out>/<stem>/<stem>.md + <stem>_content_list.json) then returns rc=0."""
    def runner(cmd, timeout):
        d = out_dir / stem
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{stem}.md").write_text(markdown, encoding="utf-8")
        (d / f"{stem}_content_list.json").write_text(
            json.dumps(content_list), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
    return runner


def test_builds_command_and_parses_output(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    out = tmp_path / "out"
    seen = {}
    content = [{"type": "text", "text": "hello", "page_idx": 0}]

    def runner(cmd, timeout):
        seen["cmd"] = cmd
        seen["timeout"] = timeout
        return _fake_ok(out, "paper", "# Hello\n\nworld", content)(cmd, timeout)

    md, cl = run_mineru(
        pdf, out, mineru_bin="/venv/bin/mineru",
        model_source="modelscope", timeout=600, runner=runner,
    )
    assert md == "# Hello\n\nworld"
    assert cl == content
    cmd = seen["cmd"]
    assert cmd[0] == "/venv/bin/mineru"
    assert "-p" in cmd and str(pdf) in cmd
    assert "-o" in cmd and str(out) in cmd
    assert "-b" in cmd and "hybrid-engine" in cmd
    assert "--effort" in cmd and "high" in cmd
    assert "--source" in cmd and "modelscope" in cmd
    assert seen["timeout"] == 600


@pytest.mark.parametrize("name", ["paper.pdf", "notes.docx", "deck.pptx"])
def test_command_is_format_agnostic(tmp_path: Path, name: str):
    # mineru -p <file> 按路径自动识别格式;pdf/docx/pptx 走同一条命令。
    src = tmp_path / name
    src.write_bytes(b"x")
    out = tmp_path / "out"
    stem = src.stem
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"] = cmd
        return _fake_ok(out, stem, "# ok\n\nbody",
                        [{"type": "text", "text": "ok", "page_idx": 0}])(cmd, timeout)

    md, cl = run_mineru(
        src, out, mineru_bin="mineru",
        model_source="modelscope", timeout=600, runner=runner,
    )
    assert md == "# ok\n\nbody"
    cmd = seen["cmd"]
    assert "-p" in cmd and str(src) in cmd
    assert "-b" in cmd and "hybrid-engine" in cmd  # 三类格式标志一致


def test_nonzero_exit_raises(tmp_path: Path):
    pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
    def runner(cmd, timeout):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom")
    with pytest.raises(ExtractionFailed):
        run_mineru(pdf, tmp_path / "o", mineru_bin="mineru",
                   model_source="modelscope", timeout=10, runner=runner)


def test_timeout_raises(tmp_path: Path):
    pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
    def runner(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)
    with pytest.raises(ExtractionFailed):
        run_mineru(pdf, tmp_path / "o", mineru_bin="mineru",
                   model_source="modelscope", timeout=1, runner=runner)


def test_missing_output_raises(tmp_path: Path):
    pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
    def runner(cmd, timeout):
        # rc=0 但没有写任何输出文件
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    with pytest.raises(ExtractionFailed):
        run_mineru(pdf, tmp_path / "o", mineru_bin="mineru",
                   model_source="modelscope", timeout=10, runner=runner)


def test_empty_markdown_raises(tmp_path: Path):
    pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
    out = tmp_path / "o"
    def runner(cmd, timeout):
        return _fake_ok(out, "p", "   \n  ", [])(cmd, timeout)
    with pytest.raises(ExtractionFailed):
        run_mineru(pdf, out, mineru_bin="mineru",
                   model_source="modelscope", timeout=10, runner=runner)
