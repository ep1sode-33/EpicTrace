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


def _conversation(client: TestClient, pid: int) -> int:
    return client.post(f"/api/projects/{pid}/conversations", json={"title": "旧标题"}).json()["id"]


# ---- conversation rename ----

def test_rename_conversation_updates_title(client, tmp_path):
    pid = _project(client, tmp_path)
    cid = _conversation(client, pid)
    resp = client.patch(f"/api/conversations/{cid}", json={"title": "新标题"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "新标题"
    assert resp.json()["id"] == cid
    # 重新拉列表确认已落库。
    listed = client.get(f"/api/projects/{pid}/conversations").json()
    assert listed[0]["title"] == "新标题"


def test_rename_conversation_trims_whitespace(client, tmp_path):
    pid = _project(client, tmp_path)
    cid = _conversation(client, pid)
    resp = client.patch(f"/api/conversations/{cid}", json={"title": "  去空白  "})
    assert resp.status_code == 200
    assert resp.json()["title"] == "去空白"


def test_rename_conversation_empty_is_400(client, tmp_path):
    pid = _project(client, tmp_path)
    cid = _conversation(client, pid)
    assert client.patch(f"/api/conversations/{cid}", json={"title": "   "}).status_code == 400
    # 标题未被改坏。
    assert client.get(f"/api/projects/{pid}/conversations").json()[0]["title"] == "旧标题"


def test_rename_conversation_clamps_maxlen(client, tmp_path):
    pid = _project(client, tmp_path)
    cid = _conversation(client, pid)
    resp = client.patch(f"/api/conversations/{cid}", json={"title": "标" * 100})
    assert resp.status_code == 200
    assert len(resp.json()["title"]) == 30


def test_rename_unknown_conversation_404(client):
    assert client.patch("/api/conversations/999999", json={"title": "x"}).status_code == 404
