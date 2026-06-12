from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import Conversation, Project
from epictrace.services.references import ReferenceService
from epictrace.services.source import SourceService


def test_get_attachment_text_returns_cached_external(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        p = Project(title="P", folder_path=str(tmp_path)); s.add(p); s.flush()
        c = Conversation(project_id=p.id, title="t"); s.add(c); s.flush(); cid = c.id
    f = tmp_path / "note.md"; f.write_text("页表把虚拟地址映射到物理地址", encoding="utf-8")
    ref = ReferenceService(db).add_external(cid, str(f), context_window=1_000_000)
    out = SourceService(db).get_attachment_text(ref["id"])
    assert out["filename"] == "note.md" and out["text"].startswith("页表")
    assert out["path"].endswith("note.md")
