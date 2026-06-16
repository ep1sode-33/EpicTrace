from __future__ import annotations

from pathlib import Path

import logging

from epictrace.db import Database
from epictrace.interfaces.organizer import Organizer, PassthroughOrganizer
from epictrace.models import CaptureSession, IngestRecord
from epictrace.services.errors import (
    CaptureSessionNotFound,
    SessionAlreadyOrganized,
    SessionNotStaged,
)
from epictrace.services.ingest import IngestService

_log = logging.getLogger("epictrace")


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
            # 只允许 staged 的会话归类:recording(录制中)不应中途归类;其它非 staged 同理。
            if sess.status != "staged":
                raise SessionNotStaged()
            staging = Path(sess.staging_dir)
            events = list(sess.events)
            s.expunge_all()

        # 幂等重试:先清掉本 session 之前留下的入库记录(可能来自上次部分失败的归类),
        # 干净重来,避免重试时产生重复记录。注:本期不删盘上已复制的文件(派生可重扫)。
        with self._db.session() as s:
            for old in s.query(IngestRecord).filter_by(source_session_id=session_id).all():
                s.delete(old)

        proposal = self._organizer.propose(sess, events, hint_project_id=project_id)

        staging_root = staging.resolve()
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
        # 2) 截图文件 ingest 进 Project(本期无图像处理器 → extracted_text 为空)。
        #    rel 来自事件 payload(任意字符串):解析为绝对路径后必须落在 staging 之内,
        #    否则视为路径穿越 / 越权,跳过不入库(防把 staging 外文件拽进 Project)。
        for rel in proposal.screenshot_rel_paths:
            shot = (staging / rel).resolve()
            if not _under(shot, staging_root):
                _log.warning("跳过越界截图路径(不在 staging 内): %r", rel)
                continue
            if not shot.is_file():
                continue
            records.append(self._ingest.ingest_file(
                project_id=project_id, source_path=str(shot),
                ingest_method="session", description=f"采集自 session {session_id}",
                source_session_id=session_id,
            ))
        # 3) 标记 organized
        with self._db.session() as s:
            s.get(CaptureSession, session_id).status = "organized"
        return records


def _under(path: Path, root: Path) -> bool:
    """path(已 resolve)是否在 root(已 resolve)之内,或恰为 root。"""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
