from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, ConversationReference, Project


def _db(tmp_path: Path) -> Database:
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all(); return db


def test_reference_persists_and_cascades_with_conversation(tmp_path: Path):
    db = _db(tmp_path)
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush()
        s.add(ConversationReference(
            conversation_id=c.id, kind="external", display_name="报告.pdf",
            source_path="/x/报告.pdf", extracted_text="正文", text_chars=2, mode="fulltext",
        ))
        cid = c.id
    with db.session() as s:
        refs = s.query(ConversationReference).filter_by(conversation_id=cid).all()
        assert len(refs) == 1 and refs[0].detached is False and refs[0].mode == "fulltext"
        s.delete(s.get(Conversation, cid))
    with db.session() as s:
        assert s.query(ConversationReference).count() == 0
