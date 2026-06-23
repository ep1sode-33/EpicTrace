"""Run 溯源指纹:让每个 run 自描述、语义不同的 run 可区分。

修审计「config.json 只存 config_hash,5 个 run 撞同一 hash;改 prompt 这种真正影响结果的变更
根本不进 hash」:run_hash = hash(config_hash + 影响结果的源码指纹 + 数据集指纹),改 LOOP_SYS /
切块 / 检索代码后 code_fingerprint 变 → run_hash 变 → 归档里一眼分得清。
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]   # scripts/rag_eval/provenance.py → backend/

# 影响生成/检索结果的源文件:agent prompt+工具+答案+引用、检索管线、切块。改这些 → 指纹变。
_CODE_FILES = [
    "epictrace/agent/react.py", "epictrace/agent/prompts.py", "epictrace/agent/tools.py",
    "epictrace/agent/answer.py", "epictrace/agent/citations.py",
    "epictrace/retrieval/pipeline.py", "epictrace/retrieval/dense.py",
    "epictrace/retrieval/sparse.py", "epictrace/retrieval/fuse.py", "epictrace/retrieval/rerank.py",
    "epictrace/indexing/chunker.py",
]


def file_fingerprint(paths) -> str:
    """对一组文件(路径 + 内容)算 12-hex 指纹;缺失文件以占位计入(故增删文件也改指纹)。"""
    h = hashlib.sha256()
    for p in sorted(str(x) for x in paths):
        pp = Path(p)
        h.update(p.encode("utf-8"))
        h.update(pp.read_bytes() if pp.is_file() else b"<missing>")
        h.update(b"\x00")
    return h.hexdigest()[:12]


def code_fingerprint(root: str | Path | None = None) -> str:
    base = Path(root) if root is not None else _BACKEND
    return file_fingerprint([base / f for f in _CODE_FILES])


def dataset_fingerprint(golden_path: str | Path) -> str:
    p = Path(golden_path)
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.is_file() else "none"


def git_sha(root: str | Path | None = None) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=str(root or _BACKEND), capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def run_hash(config_hash: str, code_fp: str, dataset_fp: str) -> str:
    """run 身份 = 配置 + 代码 + 数据 三者的合并指纹(归档目录前缀,语义不同的 run 不再撞)。"""
    return hashlib.sha256(f"{config_hash}|{code_fp}|{dataset_fp}".encode("utf-8")).hexdigest()[:12]
