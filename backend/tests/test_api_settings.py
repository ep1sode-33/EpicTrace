import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database


@pytest.fixture()
def app_client(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    return TestClient(create_app(db=db))


def test_settings_put_then_get_masks_key(app_client):
    r = app_client.put("/api/settings", json={"chat_llm": {"base_url": "http://x", "api_key": "secret", "model": "m"}})
    assert r.status_code == 200
    got = app_client.get("/api/settings").json()
    assert got["chat_llm"]["model"] == "m" and got["chat_llm"]["api_key_set"] is True
    assert "secret" not in str(got)
