from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Callable

from epictrace.media.errors import ExtractionFailed

# 注入点:默认用 subprocess.run;测试传假 runner 完全不碰真 mineru。
Runner = Callable[[list[str], int], subprocess.CompletedProcess]
ProgressCb = Callable[[str], None]

_log = logging.getLogger("epictrace")

# mineru 的 tqdm 进度条形如 `Predict:  41%|███   | 12/29 [00:30<00:42, ...]`;
# 抓「阶段名 + 已完成/总数」转成简洁中文。tqdm 把进度写到 stderr(回车刷新同一行)。
_TQDM_RE = re.compile(r"^\s*([A-Za-z][\w .\-]*?)\s*:\s*\d+%\|.*?\|\s*(\d+)\s*/\s*(\d+)")
# 阶段切换日志(非 tqdm 行):识别常见阶段关键词(Layout/OCR/Processing pages 等)。
_STAGE_KEYWORDS = (
    ("Processing pages", "处理页面"),
    ("Layout", "版面分析"),
    ("OCR", "文字识别"),
    ("Reading order", "阅读顺序"),
    ("Table", "表格识别"),
    ("Formula", "公式识别"),
    ("Predict", "解析中"),
)


def parse_progress_line(line: str) -> str | None:
    """把 mineru 的一行 stderr 解析成简洁的人类可读进度串;无可识别信息 → None。

    优先匹配 tqdm 进度条(`Predict: 41%|...| 12/29` → "解析中 12/29");否则按阶段
    关键词识别阶段切换日志(返回中文阶段名)。纯属解析,不碰子进程,便于单测。
    """
    text = line.strip()
    if not text:
        return None
    m = _TQDM_RE.match(text)
    if m:
        stage_raw, done, total = m.group(1).strip(), m.group(2), m.group(3)
        stage = _stage_label(stage_raw) or stage_raw
        return f"{stage} {done}/{total}"
    # 非 tqdm:阶段切换日志(行内含关键词即报该阶段;不带计数)。
    for needle, label in _STAGE_KEYWORDS:
        if needle.lower() in text.lower():
            return label
    return None


def _stage_label(stage_raw: str) -> str | None:
    for needle, label in _STAGE_KEYWORDS:
        if needle.lower() in stage_raw.lower():
            return label
    return None


def stream_progress(lines: Iterable[str], progress_cb: ProgressCb) -> None:
    """逐行解析进度并回调(去重:同一进度串只报一次,避免 tqdm 刷屏)。

    抽出独立函数以便用假行序列单测,无需真 mineru。
    """
    last: str | None = None
    for raw in lines:
        # tqdm 用 \r 在同一物理行刷新;按 \r 和 \n 双重切分,逐段解析。
        for piece in re.split(r"[\r\n]+", raw):
            msg = parse_progress_line(piece)
            if msg is not None and msg != last:
                progress_cb(msg)
                last = msg


def _default_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, timeout=timeout, capture_output=True, text=True, check=False
    )


def _streaming_runner(
    cmd: list[str], timeout: int, progress_cb: ProgressCb
) -> subprocess.CompletedProcess:
    """跑 mineru 并逐行流式读取 stderr(tqdm 进度 + 阶段日志走 stderr),边读边回调进度。

    仍把完整 stdout/stderr 收齐返回(供最终结果/错误消息用),行为与 _default_runner
    的返回结构一致(returncode/stdout/stderr)。仅当传入 progress_cb 时走此路。
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
    )
    err_lines: list[str] = []

    def _consume() -> Iterable[str]:
        # 边消费 stderr 边喂给上层解析,同时留底以拼回完整 stderr。
        assert proc.stderr is not None
        for line in proc.stderr:
            err_lines.append(line)
            yield line

    try:
        stream_progress(_consume(), progress_cb)
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    return subprocess.CompletedProcess(
        cmd, proc.returncode, stdout=out or "", stderr="".join(err_lines)
    )


def run_mineru(
    src_path: Path,
    out_dir: Path,
    *,
    mineru_bin: str,
    model_source: str,
    timeout: int,
    runner: Runner | None = None,
    progress_cb: ProgressCb | None = None,
) -> tuple[str, list]:
    """跑 MinerU 子进程(hybrid-engine, effort=high),读 markdown + content_list。

    src_path 可为 pdf/docx/pptx —— mineru 按文件路径自动识别格式,命令对三类完全
    一致(此函数不分支)。
    失败语义(无回退):非零退出 / 超时 / 缺输出 / 空文本 → ExtractionFailed。
    runner 注入便于测试(默认 subprocess.run)。
    progress_cb 给定且未注入 runner 时,走 Popen 流式读 stderr 把进度逐条回调
    (e.g. "解析中 12/29");注入了 runner(测试)或 progress_cb 为 None 时,行为与
    既往完全一致(一次性 subprocess.run)。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src_path.stem
    cmd = [
        mineru_bin,
        "-p", str(src_path),
        "-o", str(out_dir),
        "-b", "hybrid-engine",
        "--effort", "high",
        "--source", model_source,
    ]
    # 注入了 runner(测试)→ 一律用它(不流式);否则:有 progress_cb 走流式 Popen,
    # 无则一次性 subprocess.run(默认行为不变)。
    if runner is not None:
        run: Runner = runner
    elif progress_cb is not None:
        run = lambda c, t: _streaming_runner(c, t, progress_cb)  # noqa: E731
    else:
        run = _default_runner
    try:
        proc = run(cmd, timeout)
    except subprocess.TimeoutExpired as e:
        raise ExtractionFailed(f"mineru timed out after {timeout}s") from e
    except OSError as e:  # 二进制缺失/不可执行
        raise ExtractionFailed(f"mineru could not be launched: {e}") from e
    if proc.returncode != 0:
        raise ExtractionFailed(
            f"mineru exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
        )
    # MinerU 把输出写在 backend 模式子目录里(hybrid→hybrid_auto、pipeline→auto、
    # vlm→vlm,名字随后端/版本变),不是直接在 <out>/<stem>/<stem>.md。递归找名为
    # <stem>.md 的文件才稳;按文件名精确匹配,避免 stem 里的 []/* 等被当 glob 模式。
    md_path = next(
        (p for p in out_dir.rglob("*.md") if p.name == f"{stem}.md"), None
    )
    if md_path is None:
        raise ExtractionFailed(f"mineru produced no markdown under {out_dir}")
    cl_path = md_path.parent / f"{stem}_content_list.json"
    markdown = md_path.read_text(encoding="utf-8", errors="replace")
    if not markdown.strip():
        raise ExtractionFailed("mineru produced empty markdown")
    # content_list 是派生/可选的 provenance(可由重跑 MinerU 重建):缺失/损坏不应
    # 让提取失败而丢掉好的 markdown 文本——记一条 warning(不静默吞),返回空 list。
    content_list: list = []
    if cl_path.exists():
        try:
            content_list = json.loads(cl_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            _log.warning(
                "mineru content_list at %s is unreadable/corrupt (%s); "
                "extraction succeeds with empty provenance", cl_path, e,
            )
            content_list = []
    else:
        _log.warning(
            "mineru produced no content_list at %s; "
            "extraction succeeds with empty provenance", cl_path,
        )
    return markdown, content_list
