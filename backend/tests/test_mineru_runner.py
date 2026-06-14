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


def test_missing_content_list_succeeds_with_empty_and_warns(tmp_path: Path, caplog):
    """content_list 缺失:provenance 是可选/派生,不能因此丢掉好的提取文本——
    返回成功 + 空 content_list,并记一条 warning(不静默吞)。"""
    pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
    out = tmp_path / "o"

    def runner(cmd, timeout):
        d = out / "p"
        d.mkdir(parents=True, exist_ok=True)
        (d / "p.md").write_text("# real text\n\nbody", encoding="utf-8")
        # 故意不写 p_content_list.json
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    import logging
    with caplog.at_level(logging.WARNING, logger="epictrace"):
        md, cl = run_mineru(pdf, out, mineru_bin="mineru",
                            model_source="modelscope", timeout=10, runner=runner)
    assert md == "# real text\n\nbody"
    assert cl == []
    assert any("content_list" in r.message for r in caplog.records)


def test_corrupt_content_list_succeeds_with_empty_and_warns(tmp_path: Path, caplog):
    """content_list 损坏(非法 JSON):同样返回成功 + 空 content_list + warning,绝不 raise。"""
    pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
    out = tmp_path / "o"

    def runner(cmd, timeout):
        d = out / "p"
        d.mkdir(parents=True, exist_ok=True)
        (d / "p.md").write_text("# real text\n\nbody", encoding="utf-8")
        (d / "p_content_list.json").write_text("{not valid json", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    import logging
    with caplog.at_level(logging.WARNING, logger="epictrace"):
        md, cl = run_mineru(pdf, out, mineru_bin="mineru",
                            model_source="modelscope", timeout=10, runner=runner)
    assert md == "# real text\n\nbody"
    assert cl == []
    assert any("content_list" in r.message for r in caplog.records)


def test_finds_md_in_backend_subdir(tmp_path: Path):
    """真实布局回归:mineru 把输出写在 <out>/<stem>/hybrid_auto/ 这个 backend 模式
    子目录里(名字随后端变:pipeline→auto、vlm→vlm、hybrid→hybrid_auto),不是直接
    <out>/<stem>/<stem>.md。runner 必须能在子目录里找到 .md + content_list,
    否则真实 PDF 一律报 'produced no markdown'(就是线上那个 400)。"""
    pdf = tmp_path / "the-illusion-of-thinking.pdf"
    pdf.write_bytes(b"%PDF")
    out = tmp_path / "o"
    stem = "the-illusion-of-thinking"
    content = [{"type": "text", "text": "abstract", "page_idx": 0}]

    def runner(cmd, timeout):
        d = out / stem / "hybrid_auto"  # ← 真实的 backend 子目录层级
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{stem}.md").write_text("# The Illusion\n\nbody", encoding="utf-8")
        (d / f"{stem}_content_list.json").write_text(
            json.dumps(content), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    md, cl = run_mineru(pdf, out, mineru_bin="mineru",
                        model_source="modelscope", timeout=600, runner=runner)
    assert md == "# The Illusion\n\nbody"
    assert cl == content
