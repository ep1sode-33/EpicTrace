import json
import subprocess
import threading
from pathlib import Path

import pytest

from epictrace.media.errors import ExtractionFailed
from epictrace.media.mineru_runner import (
    parse_progress_line,
    run_mineru,
    stream_progress,
)


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
    assert "--effort" in cmd and "high" in cmd  # default effort is high (standalone call)
    assert "--source" in cmd and "modelscope" in cmd
    assert seen["timeout"] == 600


def test_effort_param_lands_in_command(tmp_path: Path):
    # 传入的 effort 应被写进命令(默认 high,可被覆盖为 medium 等)。
    pdf = tmp_path / "paper.pdf"; pdf.write_bytes(b"%PDF")
    out = tmp_path / "out"
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"] = cmd
        return _fake_ok(out, "paper", "# ok\n\nbody", [])(cmd, timeout)

    run_mineru(
        pdf, out, mineru_bin="mineru", model_source="modelscope",
        timeout=600, effort="medium", runner=runner,
    )
    cmd = seen["cmd"]
    i = cmd.index("--effort")
    assert cmd[i + 1] == "medium"
    assert "high" not in cmd


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


def test_parse_progress_tqdm_line():
    # tqdm 进度条 → "解析中 12/29"(Predict 阶段映射为「解析中」)。
    line = "Predict:  41%|████      | 12/29 [00:30<00:42,  2.40s/it]"
    assert parse_progress_line(line) == "解析中 12/29"


def test_parse_progress_stage_keyword_line():
    # 非 tqdm 的阶段日志:识别关键词,返回中文阶段名。
    assert parse_progress_line("Processing pages of document") == "处理页面"
    assert parse_progress_line("Running Layout detection") == "版面分析"
    assert parse_progress_line("OCR on region") == "文字识别"


def test_parse_progress_unrecognized_returns_none():
    assert parse_progress_line("") is None
    assert parse_progress_line("loading weights from disk") is None


def test_stream_progress_parses_and_dedups():
    """假行序列(含 \\r 刷新与重复)→ progress_cb 拿到去重后的解析进度串。"""
    seen: list[str] = []
    lines = [
        "Loading models...\n",
        # tqdm 用 \r 在一行刷新多帧;同一进度只报一次,跨帧推进各报一次。
        "Predict:   0%|          | 0/29 [00:00<?, ?it/s]\r"
        "Predict:  41%|████      | 12/29 [00:30<00:42,  2.40s/it]\r",
        "Predict:  41%|████      | 12/29 [00:31<00:41,  2.41s/it]\n",  # 重复 → 不再报
        "Predict: 100%|██████████| 29/29 [01:10<00:00,  2.41s/it]\n",
        "Processing pages\n",
    ]
    stream_progress(lines, seen.append)
    assert seen == ["解析中 0/29", "解析中 12/29", "解析中 29/29", "处理页面"]


def test_default_runner_path_streams_stderr_to_progress_cb(tmp_path: Path, monkeypatch):
    """progress_cb 给定且未注入 runner → 走 Popen 流式路:逐行读 stderr 喂解析,
    仍收齐 stdout/stderr 并产出 markdown。用假 Popen,完全不碰真 mineru。"""
    pdf = tmp_path / "paper.pdf"; pdf.write_bytes(b"%PDF")
    out = tmp_path / "out"

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            self._cmd = cmd
            # stdout 不再被 runner 读取(stdout=DEVNULL,避免管道死锁);进度全走 stderr。
            assert kwargs.get("stdout") is subprocess.DEVNULL
            self.returncode = None

            def _gen():
                # mineru 收尾:在 stderr 流结束(EOF)前写出期望的产物树,
                # 这样 runner 在 stderr 耗尽后 wait() + 读输出文件即得 markdown。
                yield "Loading models...\n"
                yield "Predict:  41%|████| 12/29 [00:30<00:42,  2.40s/it]\n"
                yield "Predict: 100%|████| 29/29 [01:10<00:00,  2.40s/it]\n"
                d = out / "paper"
                d.mkdir(parents=True, exist_ok=True)
                (d / "paper.md").write_text("# Hello\n\nworld", encoding="utf-8")
                (d / "paper_content_list.json").write_text(
                    json.dumps([{"type": "text", "text": "hi", "page_idx": 0}]),
                    encoding="utf-8")

            self.stderr = _gen()

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr("epictrace.media.mineru_runner.subprocess.Popen", _FakePopen)

    seen: list[str] = []
    md, cl = run_mineru(
        pdf, out, mineru_bin="mineru", model_source="modelscope",
        timeout=600, progress_cb=seen.append,
    )
    assert md == "# Hello\n\nworld"
    assert cl == [{"type": "text", "text": "hi", "page_idx": 0}]
    assert seen == ["解析中 12/29", "解析中 29/29"]


def test_streaming_runner_uses_devnull_stdout_and_returns_rc_stderr(tmp_path: Path, monkeypatch):
    """FIX 1:_streaming_runner 必须 stdout=DEVNULL(消除 stdout 管道死锁),且在 stderr
    EOF 后用 wait() 收 returncode、把累积的 stderr 原样带回(供错误消息/上层用)。
    这里 mineru 非零退出(无输出文件)→ run_mineru 据 returncode 抛 ExtractionFailed,
    错误消息里带回 stderr 内容,证明 stderr 被收齐。"""
    pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
    out = tmp_path / "out"
    captured_kwargs: dict = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured_kwargs.update(kwargs)
            self.returncode = None
            self.stderr = iter([
                "Loading models...\n",
                "boom: something failed\n",
            ])

        def wait(self, timeout=None):
            self.returncode = 2  # 非零退出
            return 2

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr("epictrace.media.mineru_runner.subprocess.Popen", _FakePopen)

    with pytest.raises(ExtractionFailed) as ei:
        run_mineru(pdf, out, mineru_bin="mineru", model_source="modelscope",
                   timeout=600, progress_cb=lambda _m: None)
    assert captured_kwargs.get("stdout") is subprocess.DEVNULL
    assert "exited 2" in str(ei.value)
    assert "boom" in str(ei.value)  # 累积的 stderr 被带回错误消息


def test_streaming_runner_enforces_timeout(tmp_path: Path, monkeypatch):
    """FIX 1:阻塞读 stderr 期间 communicate(timeout) 触发不了,必须靠 threading.Timer
    强制超时。这里用一个超短 timeout 和一个会阻塞到被 kill 的 stderr 流(确定性:用一个
    Event 让生成器卡住,直到被 kill() 释放),证明 timeout 触发 → ExtractionFailed(timed out)。"""
    pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
    out = tmp_path / "out"
    killed = threading.Event()

    class _HangingPopen:
        def __init__(self, cmd, **kwargs):
            self.returncode = None

        @property
        def stderr(self):
            def _gen():
                yield "Loading models...\n"
                # 阻塞,直到 timer 到点调用 kill()(确定性,无真实 sleep 竞争)。
                killed.wait(timeout=5)
            return _gen()

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9
            killed.set()  # 释放卡住的 stderr 生成器,让读循环 EOF 退出

    monkeypatch.setattr("epictrace.media.mineru_runner.subprocess.Popen", _HangingPopen)

    with pytest.raises(ExtractionFailed) as ei:
        run_mineru(pdf, out, mineru_bin="mineru", model_source="modelscope",
                   timeout=0.05, progress_cb=lambda _m: None)
    assert "timed out" in str(ei.value)
    assert killed.is_set()  # 确实走了 kill 路径


def test_streaming_runner_cancel_kills_and_raises(tmp_path: Path, monkeypatch):
    """FIX 2:cancel 事件在每读完一行 stderr 后被检查;一旦 set → proc.kill() 并
    抛 ExtractionFailed('cancelled')。这里第一行后即取消,证明 mineru 被杀、且不再继续读。"""
    pdf = tmp_path / "p.pdf"; pdf.write_bytes(b"%PDF")
    out = tmp_path / "out"
    cancel = threading.Event()
    killed = threading.Event()
    read_lines: list[str] = []

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            self.returncode = None

        @property
        def stderr(self):
            def _gen():
                read_lines.append("a")
                yield "Predict:  10%|█| 1/29 [..]\n"
                # 上层在第一行后会 cancel;若实现正确,这第二行不应被读到。
                read_lines.append("b")
                yield "Predict:  20%|██| 2/29 [..]\n"
            return _gen()

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9
            killed.set()

    monkeypatch.setattr("epictrace.media.mineru_runner.subprocess.Popen", _FakePopen)

    def _cb(_msg: str) -> None:
        cancel.set()  # 收到首条进度即模拟「客户端断开」

    with pytest.raises(ExtractionFailed) as ei:
        run_mineru(pdf, out, mineru_bin="mineru", model_source="modelscope",
                   timeout=600, progress_cb=_cb, cancel=cancel)
    assert "cancelled" in str(ei.value)
    assert killed.is_set()
    assert read_lines == ["a"]  # 取消后没有继续读下一行


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
