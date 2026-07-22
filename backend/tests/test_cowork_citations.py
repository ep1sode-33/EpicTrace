"""引用链下沉测试(Phase A):turn 级 chunk 池全局编号 + build_citations + citations 事件/落库。"""

import json

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.retrieval.types import RetrievedChunk
from epictrace.cowork.citations import build_citations
from epictrace.cowork.llm_client import LLMResponse, ToolCall
from epictrace.cowork.tools.builtin_retrieval import _format_chunks, build_retrieval_tools
from epictrace.cowork.tools.registry import ToolRegistry
from tests.fakes import FakeCoworkComplete


def _chunk(text, rid=1, **kw):
    return RetrievedChunk(text=text, ingest_record_id=rid, project_id=1,
                          char_start=0, char_end=len(text), source_type="folder_scan", **kw)


def _registry_with_fake_retriever(db, batches):
    """batches: 每次 retrieve 依序返回的 chunk 列表。"""
    calls = {"i": 0}

    class FakeRetriever:
        def retrieve(self, project_id, query, k=6):
            b = batches[min(calls["i"], len(batches) - 1)]
            calls["i"] += 1
            return b

    return build_retrieval_tools(
        db, get_retriever=lambda: FakeRetriever(), get_dense=lambda: None)


def test_global_numbering_across_searches(tmp_path):
    """同一 pool 上两次检索编号不重排:[1][2] 然后 [3][4](引用链唯一映射的前提)。"""
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    pool = []
    first = _format_chunks(db, [_chunk("甲"), _chunk("乙")], pool)
    second = _format_chunks(db, [_chunk("丙"), _chunk("丁")], pool)
    assert "[1]" in first and "[2]" in first and "[3]" not in first
    assert "[3]" in second and "[4]" in second
    assert len(pool) == 4


def test_format_without_pool_numbers_from_one(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    out = _format_chunks(db, [_chunk("甲")], None)
    assert "[1]" in out


def test_build_citations_maps_answer_markers():
    pool = [_chunk("甲"), _chunk("乙", capture_session_id=3, ts="2026-07-01T10:00:00")]
    cits = build_citations("见[1]与[2],[9] 无效", pool)
    assert [c["n"] for c in cits] == [1, 2]  # [9] 越界丢弃
    assert cits[1]["capture_session_id"] == 3
    assert cits[1]["ts"] == "2026-07-01T10:00:00"


def test_citations_over_sse_and_persisted(client):
    """e2e:两次 search_hybrid(编号 [1] 与 [2][3])→ 答案引用 [1][3] → citations 事件 + 落库。"""
    s = client.post("/api/cowork/sessions", json={"type": "agent"}).json()
    batches = [
        [_chunk("第一段", rid=1)],
        [_chunk("第二段", rid=2), _chunk("第三段", rid=3,
                                        capture_session_id=5, ts="2026-07-01T10:00:00")],
    ]
    tools = _registry_with_fake_retriever(client.app.state.db, batches)
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    # 直接用 CoworkService 装配,绕开路由的默认 registry(需要假 retriever)
    from epictrace.services.settings import SettingsService
    from epictrace.cowork.service import CoworkService

    fake = FakeCoworkComplete([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="search_hybrid",
                                         arguments='{"project_id":1,"query":"q1"}')]),
        LLMResponse(tool_calls=[ToolCall(id="c2", name="search_hybrid",
                                         arguments='{"project_id":1,"query":"q2"}')]),
        LLMResponse(content="根据 [1] 和 [3],结论是……"),
    ])
    svc = CoworkService(
        db=client.app.state.db,
        sessions=client.app.state.cowork_sessions,
        registry=registry,
        complete_fn=fake,
        settings=SettingsService(client.app.state.config),
        config=client.app.state.config,
        approvals=client.app.state.cowork_approvals,
    )
    events = list(svc.stream_message(s["id"], "查一下"))
    by_event = {}
    for e in events:
        by_event.setdefault(e["event"], []).append(e["data"])

    assert "citations" in by_event
    cits = json.loads(by_event["citations"][0])
    assert [c["n"] for c in cits] == [1, 3]
    assert cits[1]["ts"] == "2026-07-01T10:00:00"
    # 两次检索编号全局递增:LLM 看到的是 [1] 然后 [2][3]
    second_call_msgs = fake.calls[2][0]
    tool_texts = [m["content"] for m in second_call_msgs if m.get("role") == "tool"]
    assert "[1]" in tool_texts[0]
    assert "[2]" in tool_texts[1] and "[3]" in tool_texts[1]

    # 落库:最后一条 assistant 消息带 citations_json
    msgs = client.get(f"/api/cowork/sessions/{s['id']}/messages").json()
    assistant = [m for m in msgs if m["role"] == "assistant"][-1]
    assert assistant["citations_json"] is not None
    assert [c["n"] for c in json.loads(assistant["citations_json"])] == [1, 3]


def test_no_pool_no_citations_event(client):
    """没走检索(池空)→ 无 citations 事件,citations_json 为 null。"""
    s = client.post("/api/cowork/sessions", json={"type": "chat"}).json()
    client.app.state.cowork_complete = FakeCoworkComplete([LLMResponse(content="闲聊 [1]")])
    with client.stream("POST", f"/api/cowork/sessions/{s['id']}/messages",
                       json={"content": "hi"}) as r:
        body = "".join(r.iter_text())
    assert "event: citations" not in body
    msgs = client.get(f"/api/cowork/sessions/{s['id']}/messages").json()
    assert all(m["citations_json"] is None for m in msgs)
