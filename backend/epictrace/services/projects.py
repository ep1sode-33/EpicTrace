from __future__ import annotations

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
