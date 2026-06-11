import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.retrieval.pipeline import HybridRetriever
from epictrace.vectorstore.milvus_lite import MilvusLiteStore
from tests.fakes import FakeEmbedder, FakeLLM, FakeReranker


@pytest.fixture()
def chat_client(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    emb = FakeEmbedder()
    retriever = HybridRetriever(emb, store, FakeReranker())
    llm = FakeLLM(grade="sufficient", answer="页表用于地址映射[1]。")
    app = create_app(db=db, embedder=emb, vector_store=store, reranker=FakeReranker(),
                     llm=llm, retriever=retriever)
    return TestClient(app), db, store, emb


def test_chat_flow_creates_conversation_streams_and_cites(chat_client, tmp_path):
    client, db, store, emb = chat_client
    folder = tmp_path / "P"
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    store.upsert([{ "vector": emb.embed(["页表映射地址"])[0], "text": "页表映射地址", "ingest_record_id": 1,
                    "project_id": pid, "char_start": 0, "char_end": 6, "source_type": "folder_scan",
                    "embed_model_id": "fake" }])
    cid = client.post(f"/api/projects/{pid}/conversations", json={}).json()["id"]

    with client.stream("POST", f"/api/conversations/{cid}/messages", json={"content": "页表是什么"}) as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())
    assert "event: token" in body and "event: citations" in body and "event: done" in body

    msgs = client.get(f"/api/conversations/{cid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert json.loads(msgs[1]["citations_json"])[0]["ingest_record_id"] == 1


def test_delete_conversation_removes_it_and_its_messages(chat_client, tmp_path):
    client, db, store, emb = chat_client
    folder = tmp_path / "P"
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    store.upsert([{ "vector": emb.embed(["页表映射地址"])[0], "text": "页表映射地址", "ingest_record_id": 1,
                    "project_id": pid, "char_start": 0, "char_end": 6, "source_type": "folder_scan",
                    "embed_model_id": "fake" }])
    cid = client.post(f"/api/projects/{pid}/conversations", json={}).json()["id"]
    # 跑一轮,落 user + assistant 两条消息。
    with client.stream("POST", f"/api/conversations/{cid}/messages", json={"content": "页表是什么"}) as r:
        assert r.status_code == 200
        "".join(chunk for chunk in r.iter_text())
    assert len(client.get(f"/api/conversations/{cid}/messages").json()) == 2

    r = client.delete(f"/api/conversations/{cid}")
    assert r.status_code in (200, 204)

    # 会话从列表消失,其消息也随级联删除(查消息得 404,会话已不存在)。
    listed = client.get(f"/api/projects/{pid}/conversations").json()
    assert all(c["id"] != cid for c in listed)
    assert client.get(f"/api/conversations/{cid}/messages").status_code == 404


def test_delete_unknown_conversation_returns_404(chat_client):
    client, db, store, emb = chat_client
    assert client.delete("/api/conversations/999999").status_code == 404


def test_send_message_without_llm_configured_returns_409(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    app = create_app(db=db)  # llm=None 且 settings 从未保存(未配置)
    client = TestClient(app)
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(tmp_path / "P")}).json()["id"]
    cid = client.post(f"/api/projects/{pid}/conversations", json={}).json()["id"]
    r = client.request("POST", f"/api/conversations/{cid}/messages", json={"content": "x"})
    assert r.status_code == 409  # 未配置对话模型


def test_get_llm_allows_keyless_local_endpoint_when_configured(tmp_path):
    # 已保存设置但 api_key 为空(本地 Ollama 等):应构造出 LLM,而非 None。
    from fastapi import Request

    from epictrace.api.deps import get_llm
    from epictrace.llm.openai_compat import OpenAICompatLLM
    from epictrace.services.settings import SettingsService

    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    app = create_app(db=db)
    SettingsService(app.state.config).create_profile(
        name="local", base_url="http://localhost:11434/v1", model="qwen", api_key=""
    )
    req = Request({"type": "http", "app": app})
    llm = get_llm(req)
    assert isinstance(llm, OpenAICompatLLM)
