from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database


class _FakeProc:
    """假 worker 句柄:满足 supervisor 的 terminate/wait/kill/poll 接口,绝不起真子进程。"""

    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def poll(self):  # noqa: ANN201 — None 表示仍在跑
        return None
    def wait(self, timeout=None) -> int:  # noqa: ANN001
        return 0


@pytest.fixture(autouse=True)
def _no_real_asr_worker(monkeypatch):
    """**全局**:测试绝不 spawn 真 ASR worker 子进程。否则建带 mic/system_audio 的 session 会真起
    `python -m epictrace.asr.worker`(去下 large-v3 + 抢下载锁),漏一堆僵尸进程、还把模型下载拖死
    (真机踩过)。把 supervisor 的模块级默认 spawn 换成假句柄;start() 在调用点解析它,故对默认构造的
    AsrSupervisor 也生效。"""
    import epictrace.asr.supervisor as sup

    monkeypatch.setattr(sup, "_default_spawn", lambda argv: _FakeProc())


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
