from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.media import get_processor
from epictrace.models import IngestRecord, Project

# 硬跳过的目录(代码仓常见噪音)+ 所有点开头隐藏目录
IGNORE_DIRS = {
    "node_modules", ".git", ".venv", "venv", "env", "__pycache__",
    "dist", "build", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
}
# 仅登记这些可索引的文本/文档/代码类型(其余如二进制、媒体先跳过)
INDEXABLE_SUFFIXES = {
    ".md", ".markdown", ".txt", ".text", ".rst",
    ".pdf", ".ppt", ".pptx", ".doc", ".docx",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".json", ".yaml", ".yml", ".toml", ".csv", ".html", ".css", ".sql",
}


@dataclass(frozen=True)
class ScanResult:
    added: int
    missing: int


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_indexable(folder: Path):
    for root, dirs, files in os.walk(folder):
        # 原地裁剪:跳过忽略目录 + 隐藏目录
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            p = Path(root) / name
            if p.suffix.lower() in INDEXABLE_SUFFIXES:
                yield p


class ScanService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def scan_and_register(self, project_id: int) -> ScanResult:
        with self._db.session() as s:
            project = s.get(Project, project_id)
            if project is None:
                raise ValueError(f"project {project_id} not found")
            folder = Path(project.folder_path)

            existing_paths = {
                r.stored_path
                for r in s.execute(
                    select(IngestRecord).where(IngestRecord.project_id == project_id)
                ).scalars()
            }

            added = 0
            for p in _iter_indexable(folder):
                sp = str(p)
                if sp in existing_paths:
                    continue
                proc = get_processor(p)
                extracted = proc.process(p).text if proc is not None else ""
                s.add(
                    IngestRecord(
                        project_id=project_id,
                        original_filename=p.name,
                        stored_path=sp,
                        content_hash=_sha256(p),
                        size_bytes=p.stat().st_size,
                        mtime=p.stat().st_mtime,
                        ingest_method="folder_scan",
                        description="",
                        extracted_text=extracted,
                        indexed=False,
                    )
                )
                added += 1

            # 检测缺失:记录指向的文件已不存在
            missing = sum(
                1
                for r in s.execute(
                    select(IngestRecord).where(IngestRecord.project_id == project_id)
                ).scalars()
                if not Path(r.stored_path).exists()
            )
            return ScanResult(added=added, missing=missing)

    def list_pending(self, project_id: int) -> list[IngestRecord]:
        with self._db.session() as s:
            rows = (
                s.execute(
                    select(IngestRecord)
                    .where(IngestRecord.project_id == project_id, IngestRecord.indexed.is_(False))
                    .order_by(IngestRecord.created_at)
                )
                .scalars()
                .all()
            )
            for r in rows:
                s.expunge(r)
            return list(rows)
