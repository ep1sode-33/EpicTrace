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


def test_delete_removes_row_and_returns_path(tmp_path: Path):
    db = _db(tmp_path)
    folder = tmp_path / "CS 2506"
    svc = ProjectService(db)
    proj = svc.create(title="CS 2506", folder_path=str(folder))

    returned = svc.delete(proj.id, delete_folder=False)
    assert returned == str(folder)        # 返回 folder_path 供路由决定是否删盘
    assert svc.list() == []               # DB 行已删
    assert folder.exists()                # 默认不删盘


def test_delete_unknown_returns_none(tmp_path: Path):
    svc = ProjectService(_db(tmp_path))
    assert svc.delete(99999, delete_folder=False) is None


def test_rename_updates_title_and_keeps_folder(tmp_path: Path):
    db = _db(tmp_path)
    folder = tmp_path / "CS 2506"
    svc = ProjectService(db)
    proj = svc.create(title="CS 2506", folder_path=str(folder))

    renamed = svc.rename(proj.id, "操作系统 2506")
    assert renamed is not None
    assert renamed.title == "操作系统 2506"
    assert renamed.folder_path == str(folder)   # 磁盘路径不变
    assert folder.exists()                       # 不移动/重命名文件夹
    assert [p.title for p in svc.list()] == ["操作系统 2506"]


def test_rename_unknown_returns_none(tmp_path: Path):
    assert ProjectService(_db(tmp_path)).rename(99999, "x") is None
