from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    app = create_app(db=db)
    return TestClient(app)


def _project(client: TestClient, tmp_path: Path) -> int:
    folder = str(tmp_path / "P")
    return client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]


def _session(client: TestClient) -> int:
    return client.post("/api/cowork/sessions", json={"name": "旧标题"}).json()["id"]


# ---- cowork session rename ----

def test_rename_session_updates_name(client):
    sid = _session(client)
    resp = client.patch(f"/api/cowork/sessions/{sid}", json={"name": "新标题"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "新标题"
    assert resp.json()["id"] == sid
    # 重新拉取确认已落库。
    assert client.get(f"/api/cowork/sessions/{sid}").json()["name"] == "新标题"


def test_rename_session_trims_whitespace(client):
    sid = _session(client)
    resp = client.patch(f"/api/cowork/sessions/{sid}", json={"name": "  去空白  "})
    assert resp.status_code == 200
    assert resp.json()["name"] == "去空白"


def test_rename_session_empty_is_400(client):
    sid = _session(client)
    assert client.patch(f"/api/cowork/sessions/{sid}", json={"name": "   "}).status_code == 400
    # 标题未被改坏。
    assert client.get(f"/api/cowork/sessions/{sid}").json()["name"] == "旧标题"


def test_rename_session_over_maxlen_is_422(client):
    # 新栈契约:CoworkSessionRename schema 上限 255,超长 → 422(旧栈是钳到 30)。
    sid = _session(client)
    assert client.patch(f"/api/cowork/sessions/{sid}", json={"name": "标" * 300}).status_code == 422
    assert client.get(f"/api/cowork/sessions/{sid}").json()["name"] == "旧标题"


def test_rename_unknown_session_404(client):
    assert client.patch("/api/cowork/sessions/999999", json={"name": "x"}).status_code == 404


# ---- project rename ----

def test_rename_project_updates_title(client, tmp_path):
    folder = str(tmp_path / "P")
    pid = client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]
    resp = client.patch(f"/api/projects/{pid}", json={"title": "重命名后的项目"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "重命名后的项目"
    assert resp.json()["folder_path"] == folder   # 路径不变


def test_rename_project_keeps_folder_path_unchanged(client, tmp_path):
    folder = str(tmp_path / "P")
    pid = client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"title": "新名字"})
    listed = client.get("/api/projects").json()
    assert listed[0]["title"] == "新名字"
    assert listed[0]["folder_path"] == folder      # 磁盘路径一字未改
    assert Path(folder).exists()                    # 文件夹仍在原处


def test_rename_project_trims_and_rejects_empty(client, tmp_path):
    folder = str(tmp_path / "P")
    pid = client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]
    assert client.patch(f"/api/projects/{pid}", json={"title": "  整理  "}).json()["title"] == "整理"
    assert client.patch(f"/api/projects/{pid}", json={"title": "   "}).status_code == 400


def test_rename_project_clamps_maxlen(client, tmp_path):
    folder = str(tmp_path / "P")
    pid = client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]
    resp = client.patch(f"/api/projects/{pid}", json={"title": "项" * 100})
    assert resp.status_code == 200
    assert len(resp.json()["title"]) == 30


def test_rename_unknown_project_404(client):
    assert client.patch("/api/projects/999999", json={"title": "x"}).status_code == 404
