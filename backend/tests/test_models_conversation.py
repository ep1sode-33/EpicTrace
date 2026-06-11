from pathlib import Path

from sqlalchemy import select

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Message, Project


def test_conversation_messages_and_cascade(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path / "P")); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="问页表"); s.add(c); s.flush()
        s.add(Message(conversation_id=c.id, role="user", content="页表是啥"))
        s.add(Message(conversation_id=c.id, role="assistant", content="答[1]", citations_json="[]"))
        pid, cid = p.id, c.id
    with db.session() as s:
        msgs = list(s.execute(select(Message).where(Message.conversation_id == cid)).scalars())
        assert len(msgs) == 2 and msgs[0].role == "user"
    # 删项目级联删会话+消息
    with db.session() as s:
        s.delete(s.get(Project, pid))
    with db.session() as s:
        assert s.execute(select(Conversation)).scalars().first() is None
        assert s.execute(select(Message)).scalars().first() is None
