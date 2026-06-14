from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Callable

from epictrace.media.errors import ExtractionFailed

# 注入点:默认用 subprocess.run;测试传假 runner 完全不碰真 mineru。
Runner = Callable[[list[str], int], subprocess.CompletedProcess]

_log = logging.getLogger("epictrace")


def _default_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, timeout=timeout, capture_output=True, text=True, check=False
    )


def run_mineru(
    src_path: Path,
    out_dir: Path,
    *,
    mineru_bin: str,
    model_source: str,
    timeout: int,
    runner: Runner | None = None,
) -> tuple[str, list]:
    """跑 MinerU 子进程(hybrid-engine, effort=high),读 markdown + content_list。

    src_path 可为 pdf/docx/pptx —— mineru 按文件路径自动识别格式,命令对三类完全
    一致(此函数不分支)。
    失败语义(无回退):非零退出 / 超时 / 缺输出 / 空文本 → ExtractionFailed。
    runner 注入便于测试(默认 subprocess.run)。
    """
    run = runner or _default_runner
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
    result_dir = out_dir / stem
    md_path = result_dir / f"{stem}.md"
    cl_path = result_dir / f"{stem}_content_list.json"
    if not md_path.exists():
        raise ExtractionFailed(f"mineru produced no markdown at {md_path}")
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
