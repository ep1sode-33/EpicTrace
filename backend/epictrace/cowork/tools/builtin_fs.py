"""文件系统内置工具(需求 3 工具清单):list_projects / list_files / read_file / search_text。

所有工具限定在用户已注册的项目文件夹内(路径解析后必须仍在项目目录下),
不允许越界访问任意主机路径。工厂闭包捕获 db,与现有服务的注入惯例一致。
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.models import IngestRecord, Project
from epictrace.cowork.tools.registry import ToolDef

_MAX_ENTRIES = 200
_MAX_DEPTH = 4
_READ_LIMIT = 20000
_MATCH_LIMIT = 50
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".DS_Store"}


def _project(db: Database, project_id: int) -> Project | None:
    with db.session() as s:
        return s.get(Project, project_id)


def _resolve_in(base: Path, rel: str) -> Path | None:
    """把 rel 解析到 base 之下;越界(含 ..)返回 None。"""
    try:
        target = (base / rel).resolve()
        target.relative_to(base.resolve())
        return target
    except (OSError, ValueError):
        return None


def build_fs_tools(db: Database) -> list[ToolDef]:
    def list_projects() -> str:
        with db.session() as s:
            rows = s.execute(select(Project).order_by(Project.id)).scalars()
            out = [
                {"id": p.id, "title": p.title, "folder": p.folder_path}
                for p in rows
            ]
        if not out:
            return "当前没有任何项目。可用 create_project 创建。"
        lines = [f"- id={p['id']} | {p['title']} | {p['folder']}" for p in out]
        return "项目列表:\n" + "\n".join(lines)

    def list_files(project_id: int, path: str = ".") -> str:
        proj = _project(db, project_id)
        if proj is None:
            return f"Error: project {project_id} not found"
        base = Path(proj.folder_path)
        target = _resolve_in(base, path)
        if target is None or not target.exists():
            return f"Error: path not found in project: {path}"
        lines: list[str] = []
        count = 0

        def walk(d: Path, depth: int) -> None:
            nonlocal count
            if depth > _MAX_DEPTH or count >= _MAX_ENTRIES:
                return
            try:
                children = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name))
            except OSError:
                return
            for c in children:
                if count >= _MAX_ENTRIES:
                    return
                if c.name in _SKIP_DIRS:
                    continue
                # 目录软链不递归(codex review R3:is_dir() 会跟随软链,
                # 会把项目根之外的目录枚举出来——绕过 _resolve_in 守卫)
                if c.is_dir() and c.is_symlink():
                    rel = c.relative_to(base)
                    lines.append("  " * depth + "🔗 " + str(rel) + " (软链,不展开)")
                    count += 1
                    continue
                rel = c.relative_to(base)
                lines.append("  " * depth + ("📁 " if c.is_dir() else "") + str(rel))
                count += 1
                if c.is_dir():
                    walk(c, depth + 1)

        walk(target, 0)
        if not lines:
            return f"{path}:空目录"
        suffix = f"\n…(超过 {_MAX_ENTRIES} 条,已截断)" if count >= _MAX_ENTRIES else ""
        return f"项目 {proj.title} 的目录结构({path}):\n" + "\n".join(lines) + suffix

    def read_file(project_id: int, path: str, offset: int = 0, limit: int = 8000) -> str:
        proj = _project(db, project_id)
        if proj is None:
            return f"Error: project {project_id} not found"
        target = _resolve_in(Path(proj.folder_path), path)
        if target is None or not target.is_file():
            return f"Error: file not found in project: {path}"
        limit = max(1, min(int(limit), _READ_LIMIT))
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"Error: cannot read {path}: {e}"
        total = len(text)
        chunk = text[offset: offset + limit]
        note = f"(全文 {total} 字符,当前 {offset}-{offset + len(chunk)})" if total > len(chunk) else ""
        return f"{path} {note}\n{chunk}"

    def search_text(project_id: int, pattern: str, path: str = ".") -> str:
        proj = _project(db, project_id)
        if proj is None:
            return f"Error: project {project_id} not found"
        base = _resolve_in(Path(proj.folder_path), path)
        if base is None or not base.exists():
            return f"Error: path not found in project: {path}"
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            rx = re.compile(re.escape(pattern), re.IGNORECASE)
        matches: list[str] = []
        files = [base] if base.is_file() else (
            p for p in base.rglob("*") if p.is_file() and not (set(p.parts) & _SKIP_DIRS)
        )
        for f in files:
            if len(matches) >= _MATCH_LIMIT:
                break
            try:
                if f.stat().st_size > 2 * 1024 * 1024:
                    continue
                with open(f, encoding="utf-8", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if rx.search(line):
                            rel = f.relative_to(Path(proj.folder_path))
                            matches.append(f"{rel}:{lineno}: {line.strip()[:200]}")
                            if len(matches) >= _MATCH_LIMIT:
                                break
            except OSError:
                continue
        if not matches:
            return f"未找到匹配 {pattern!r} 的内容。"
        suffix = f"\n…(超过 {_MATCH_LIMIT} 条,已截断)" if len(matches) >= _MATCH_LIMIT else ""
        return "\n".join(matches) + suffix

    def delete_file(project_id: int, path: str) -> str:
        proj = _project(db, project_id)
        if proj is None:
            return f"Error: project {project_id} not found"
        target = _resolve_in(Path(proj.folder_path), path)
        if target is None:
            return f"Error: path escapes project folder: {path}"
        if not target.is_file():
            return f"Error: file not found in project: {path}"
        try:
            target.unlink()
        except OSError as e:
            return f"Error: cannot delete {path}: {e}"
        return f"已删除 {path}"

    return [
        ToolDef(
            name="list_projects",
            description="列出用户所有项目(id、标题、文件夹路径)。检索或读文件前先确认 project_id。",
            parameters={"type": "object", "properties": {}},
            handler=list_projects,
            permission="allow",
        ),
        ToolDef(
            name="list_files",
            description="列出某个项目的目录结构(深度有限)。了解项目组成的第一步。",
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "项目 id"},
                    "path": {"type": "string", "description": "项目内相对路径,默认根目录"},
                },
                "required": ["project_id"],
            },
            handler=list_files,
            permission="allow",
        ),
        ToolDef(
            name="read_file",
            description="读取项目内某个文本文件的内容(大文件分页读取)。",
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "path": {"type": "string", "description": "项目内相对路径"},
                    "offset": {"type": "integer", "description": "起始字符偏移,默认 0"},
                    "limit": {"type": "integer", "description": "读取字符数,默认 8000,最大 20000"},
                },
                "required": ["project_id", "path"],
            },
            handler=read_file,
            permission="allow",
        ),
        ToolDef(
            name="search_text",
            description="在项目文件中做全文搜索(支持正则),返回 文件:行号: 内容 匹配行。",
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "pattern": {"type": "string", "description": "搜索词或正则"},
                    "path": {"type": "string", "description": "限定子目录,默认全项目"},
                },
                "required": ["project_id", "pattern"],
            },
            handler=search_text,
            permission="allow",
        ),
        ToolDef(
            name="delete_file",
            description="删除项目内的某个文件(不可恢复)。数据删除类操作,每次调用都需用户确认。",
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "path": {"type": "string", "description": "项目内相对路径"},
                },
                "required": ["project_id", "path"],
            },
            handler=delete_file,
            permission="ask",
            always_allow_suppressed=True,  # 需求 7:删除类工具禁止「总是允许」
        ),
    ]
