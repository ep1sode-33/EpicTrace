from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.models import Project


class ProjectService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, title: str, folder_path: str) -> Project:
        Path(folder_path).mkdir(parents=True, exist_ok=True)
        with self._db.session() as s:
            proj = Project(title=title, folder_path=folder_path)
            s.add(proj)
            s.flush()
            s.refresh(proj)
            s.expunge(proj)
            return proj

    def list(self) -> list[Project]:
        with self._db.session() as s:
            rows = s.execute(select(Project).order_by(Project.created_at)).scalars().all()
            for r in rows:
                s.expunge(r)
            return list(rows)

    def rename(self, project_id: int, title: str) -> Project | None:
        """仅改显示标题(绝不动 folder_path / 磁盘文件夹)。项目不存在则返回 None。
        标题的 trim / 非空 / 钳长由调用方(路由)负责;本方法只写 title。"""
        with self._db.session() as s:
            proj = s.get(Project, project_id)
            if proj is None:
                return None
            proj.title = title
            s.flush()
            s.refresh(proj)
            s.expunge(proj)
            return proj

    def delete(self, project_id: int, delete_folder: bool = False) -> str | None:
        """删除项目 DB 行(IngestRecord 随 cascade 一并删除)。

        返回被删项目的 folder_path(供路由决定是否清理向量/盘);项目不存在则返回 None。
        delete_folder=True 时仅删除该项目自身的 folder_path(存在且是目录才删)。
        向量库的清理由调用方负责(路由层),保持本服务不依赖 vector store。
        """
        with self._db.session() as s:
            proj = s.get(Project, project_id)
            if proj is None:
                return None
            folder_path = proj.folder_path
            s.delete(proj)  # ingest_records 经 cascade="all, delete-orphan" 一并删除

        if delete_folder:
            path = Path(folder_path)
            # 守卫:只删这个确切路径,且必须已存在并且是目录(不跟随符号链接删别处)。
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        return folder_path
