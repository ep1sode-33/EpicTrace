from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    app = create_app(db=db)
    return TestClient(app)


@pytest.fixture()
def index_client(tmp_path):
    from epictrace.vectorstore.milvus_lite import MilvusLiteStore
    from tests.fakes import FakeEmbedder
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    app = create_app(db=db, embedder=FakeEmbedder(), vector_store=store)
    return TestClient(app)
