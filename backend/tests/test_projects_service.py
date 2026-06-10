from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.services.projects import ProjectService


def _db(tmp_path: Path) -> Database:
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    return db


def test_create_project_creates_folder_and_row(tmp_path: Path):
    db = _db(tmp_path)
    folder = tmp_path / "CS 2506"
    svc = ProjectService(db)
    proj = svc.create(title="CS 2506", folder_path=str(folder))
    assert proj.id is not None
    assert folder.exists()  # 文件夹被创建
    assert [p.title for p in svc.list()] == ["CS 2506"]


def test_list_empty(tmp_path: Path):
    assert ProjectService(_db(tmp_path)).list() == []
