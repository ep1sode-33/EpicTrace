import json
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Message, Project
from epictrace.retrieval.types import RetrievedChunk
from epictrace.services.chat import ChatService
from tests.fakes import FakeLLM


class _Retriever:
    def retrieve(self, *, project_id, query, k=6):
        return [RetrievedChunk(text="页表映射地址", ingest_record_id=1, project_id=project_id,
                               char_start=0, char_end=6, source_type="folder_scan")]


def _setup(tmp_path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path / "P")); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        cid = c.id
    return db, cid


def test_stream_emits_events_and_persists(tmp_path: Path):
    db, cid = _setup(tmp_path)
    svc = ChatService(db, FakeLLM(grade="sufficient", answer="地址映射靠页表[1]。"), _Retriever())
    events = list(svc.stream_answer(cid, "页表是什么"))
    kinds = [e["event"] for e in events]
    assert "status" in kinds and "token" in kinds and "citations" in kinds and kinds[-1] == "done"
    answer = "".join(e["data"] for e in events if e["event"] == "token")
    assert "页表" in answer
    cite_evt = next(e for e in events if e["event"] == "citations")
    assert json.loads(cite_evt["data"])[0]["ingest_record_id"] == 1
    # 落库:user + assistant 两条
    from sqlalchemy import select
    with db.session() as s:
        msgs = list(s.execute(select(Message).where(Message.conversation_id == cid)).scalars())
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[1].citations_json and "ingest_record_id" in msgs[1].citations_json
