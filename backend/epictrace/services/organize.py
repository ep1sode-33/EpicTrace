from __future__ import annotations

from pathlib import Path

from epictrace.db import Database
from epictrace.interfaces.organizer import Organizer, PassthroughOrganizer
from epictrace.models import CaptureSession, IngestRecord
from epictrace.services.errors import (
    CaptureSessionNotFound,
    SessionAlreadyOrganized,
)
from epictrace.services.ingest import IngestService


class OrganizeService:
    """把一个 staged session 物化进 Project 文件夹并入库(复用 IngestService)。
    organizer 默认 PassthroughOrganizer(整段归一个 Project);真·归类 Agent 后续替换。"""

    def __init__(self, db: Database, organizer: Organizer | None = None) -> None:
        self._db = db
        self._organizer = organizer or PassthroughOrganizer()
        self._ingest = IngestService(db)

    def organize(self, session_id: int, project_id: int) -> list[IngestRecord]:
        with self._db.session() as s:
            sess = s.get(CaptureSession, session_id)
            if sess is None:
                raise CaptureSessionNotFound(session_id)
            if sess.status == "organized":
                raise SessionAlreadyOrganized()
            staging = Path(sess.staging_dir)
            events = list(sess.events)
            s.expunge_all()

        proposal = self._organizer.propose(sess, events, hint_project_id=project_id)

        records: list[IngestRecord] = []
        # 1) 物化 markdown 到 staging,再 ingest 进 Project
        for filename, content in proposal.markdown_docs:
            doc = staging / filename
            doc.write_text(content, encoding="utf-8")
            records.append(self._ingest.ingest_file(
                project_id=project_id, source_path=str(doc),
                ingest_method="session", description=f"采集自 session {session_id}",
                source_session_id=session_id,
            ))
        # 2) 截图文件 ingest 进 Project(本期无图像处理器 → extracted_text 为空)
        for rel in proposal.screenshot_rel_paths:
            shot = staging / rel
            if shot.is_file():
                records.append(self._ingest.ingest_file(
                    project_id=project_id, source_path=str(shot),
                    ingest_method="session", description=f"采集自 session {session_id}",
                    source_session_id=session_id,
                ))
        # 3) 标记 organized
        with self._db.session() as s:
            s.get(CaptureSession, session_id).status = "organized"
        return records
