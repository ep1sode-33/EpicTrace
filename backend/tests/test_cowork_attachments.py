"""Phase C:references 绑 session + 附件工具(search/read_attachment)+ manifest 注入。"""

import json

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.retrieval.types import RetrievedChunk
from epictrace.cowork.tools.builtin_attachments import (
    attachment_manifest,
    build_attachment_tools,
)
from epictrace.cowork.tools.registry import ToolRegistry


@pytest.fixture()
def env(tmp_path):
    config = AppConfig(data_dir=tmp_path)
    db = Database(config)
    db.create_all()
    from epictrace.cowork.sessions import SessionManager

    sessions = SessionManager(db)
    sess = sessions.create(type="agent")
    ext = tmp_path / "附件.txt"
    ext.write_text("这是附件的全文内容,讲述了季度预算的三个要点。" * 5, encoding="utf-8")
    from epictrace.services.references import ReferenceService

    ref = ReferenceService(db).add_external(sess.id, str(ext), context_window=32768)
    return {"db": db, "sess": sess, "ref": ref, "file": ext}


def test_reference_bound_to_session(env):
    assert env["ref"]["session_id"] == env["sess"].id
    assert env["ref"]["mode"] == "fulltext"  # 小文本文件 → fulltext


def test_add_internal_rejects_wrong_project(env):
    with pytest.raises(ValueError, match="session not found"):
        from epictrace.services.references import ReferenceService

        ReferenceService(env["db"]).add_internal(9999, 1, context_window=32768)


def test_read_attachment_fulltext(env):
    registry = ToolRegistry()
    for t in build_attachment_tools(env["db"], session_id=env["sess"].id,
                                    get_attachment_retriever=lambda: None):
        registry.register(t)
    out = registry.execute("read_attachment", json.dumps({"reference_id": env["ref"]["id"]}))
    assert "附件全文" in out
    assert "季度预算" in out
    assert "[done=True]" in out


def test_read_attachment_rejects_unknown_id(env):
    registry = ToolRegistry()
    for t in build_attachment_tools(env["db"], session_id=env["sess"].id,
                                    get_attachment_retriever=lambda: None):
        registry.register(t)
    out = registry.execute("read_attachment", json.dumps({"reference_id": 9999}))
    assert out.startswith("Error:")


def test_read_attachment_pool_numbering(env):
    """read_attachment 的 chunk 旁路进 pool,编号与项目检索的全局编号一致。"""
    registry = ToolRegistry()
    for t in build_attachment_tools(env["db"], session_id=env["sess"].id,
                                    get_attachment_retriever=lambda: None):
        registry.register(t)
    ctx = {"chunk_pool": []}
    registry.execute("read_attachment", json.dumps({"reference_id": env["ref"]["id"]}), ctx=ctx)
    assert len(ctx["chunk_pool"]) == 1
    c = ctx["chunk_pool"][0]
    assert c.source_kind == "attachment"
    assert c.reference_id == env["ref"]["id"]


def test_search_attachment_no_indexed(env):
    """本会话没有 indexed 附件时给友好提示,而不是报错。"""
    registry = ToolRegistry()
    for t in build_attachment_tools(env["db"], session_id=env["sess"].id,
                                    get_attachment_retriever=lambda: None):
        registry.register(t)
    out = registry.execute("search_attachment", '{"query": "预算"}')
    assert "没有已建索引的附件" in out


def test_search_attachment_with_fake(env):
    class FakeAR:
        def retrieve(self, *, session_id, reference_ids, query, k=6, **_):
            return [RetrievedChunk(text="预算要点片段", ingest_record_id=0, project_id=0,
                                   char_start=0, char_end=6, source_type="attachment",
                                   source_kind="attachment", reference_id=reference_ids[0])]

    # 把附件标记为 indexed(模拟大文件路径)
    from epictrace.models import Reference

    with env["db"].session() as s:
        row = s.get(Reference, env["ref"]["id"])
        row.mode = "indexed"

    registry = ToolRegistry()
    for t in build_attachment_tools(env["db"], session_id=env["sess"].id,
                                    get_attachment_retriever=lambda: FakeAR()):
        registry.register(t)
    out = registry.execute("search_attachment", '{"query": "预算"}', ctx={"chunk_pool": []})
    assert "预算要点片段" in out
    assert "[1]" in out


def test_attachment_manifest(env):
    text = attachment_manifest(env["db"], env["sess"].id)
    assert "本会话附件" in text
    assert f"id={env['ref']['id']}" in text
    assert "附件.txt" in text
    # 空会话 → 空串(不注入)
    from epictrace.cowork.sessions import SessionManager

    empty = SessionManager(env["db"]).create(type="agent")
    assert attachment_manifest(env["db"], empty.id) == ""


# ---- references API(换绑到 /cowork/sessions/{sid}/references)----

def test_references_api_roundtrip(client, tmp_path):
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    f = tmp_path / "外部笔记.txt"
    f.write_text("外部文件内容,供引用。", encoding="utf-8")

    r = client.post(f"/api/cowork/sessions/{s['id']}/references",
                    json={"kind": "external", "source_path": str(f)})
    assert r.status_code == 201, r.text
    assert r.json()["session_id"] == s["id"]

    rows = client.get(f"/api/cowork/sessions/{s['id']}/references").json()
    assert len(rows) == 1 and rows[0]["display_name"] == "外部笔记.txt"

    rid = rows[0]["id"]
    assert client.delete(f"/api/cowork/sessions/{s['id']}/references/{rid}").status_code == 204
    assert client.get(f"/api/cowork/sessions/{s['id']}/references").json() == []


def test_references_api_404(client):
    assert client.get("/api/cowork/sessions/9999/references").status_code == 404


def test_delete_session_cleans_attachment_vectors(client, tmp_path):
    """删会话:有引用时清掉会话级附件向量(对齐旧对话栈语义)。"""
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    f = tmp_path / "附件x.txt"
    f.write_text("内容", encoding="utf-8")
    assert client.post(f"/api/cowork/sessions/{s['id']}/references",
                       json={"kind": "external", "source_path": str(f)}).status_code == 201

    class FakeStore:
        def __init__(self):
            self.deleted = []

        def delete(self, flt):
            self.deleted.append(flt)

    fake = FakeStore()
    client.app.state.attachment_store = fake
    assert client.delete(f"/api/cowork/sessions/{s['id']}").status_code == 204
    assert fake.deleted == [{"session_id": s["id"]}]


def test_delete_session_without_refs_skips_store(client):
    """无引用的会话删除不触碰 attachment store(重资源不白起)。"""
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()

    class ExplodingStore:
        def delete(self, flt):
            raise AssertionError("不应被调用")

    client.app.state.attachment_store = ExplodingStore()
    assert client.delete(f"/api/cowork/sessions/{s['id']}").status_code == 204
