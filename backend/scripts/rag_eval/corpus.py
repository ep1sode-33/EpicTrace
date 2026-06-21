"""把分层切片从只读源拷到冻结目录 eval-data/ + 算 sha256 manifest。绝不写源目录。"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CorpusEntry:
    src: Path
    slices: dict
    source: str = "own"


@dataclass(frozen=True)
class ManifestRow:
    rel_path: str
    sha256: str
    bytes: int
    slices: dict
    source: str


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def build_corpus(entries: list[CorpusEntry], dest: Path, corpus_version: str) -> list[ManifestRow]:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    rows: list[ManifestRow] = []
    for e in entries:
        src = Path(e.src)
        digest = _sha256(src)
        # sha 前缀防同名覆盖;保留原扩展名供 MediaProcessor 识别类型。
        rel = f"{digest[:8]}-{src.name}"
        shutil.copy2(src, dest / rel)   # copy2 只读源、写新文件
        rows.append(ManifestRow(rel_path=rel, sha256=digest,
                                bytes=src.stat().st_size, slices=e.slices, source=e.source))
    manifest = dest / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"corpus_version": corpus_version}, ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    return rows


def load_manifest(path: str | Path) -> list[ManifestRow]:
    rows: list[ManifestRow] = []
    with Path(path).open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line or i == 0:        # 第 0 行是 {corpus_version}
                continue
            rows.append(ManifestRow(**json.loads(line)))
    return rows
