from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from epictrace.api.deps import get_db
from epictrace.db import Database
from epictrace.models import Conversation
from epictrace.schemas import ReferenceCreate, ReferenceOut
from epictrace.services.references import ReferenceService
from epictrace.services.settings import SettingsService

router = APIRouter(tags=["references"])  # /api 由 app 工厂统一挂载


def _require_conv(db: Database, cid: int) -> None:
    with db.session() as s:
        if s.get(Conversation, cid) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")


def _context_window(request: Request) -> int:
    chat = SettingsService(request.app.state.config).get_chat_llm()
    return chat.context_window if chat else 32768


@router.get("/conversations/{cid}/references", response_model=list[ReferenceOut])
def list_references(cid: int, db: Database = Depends(get_db)):
    _require_conv(db, cid)
    return ReferenceService(db).list_active(cid)


@router.post("/conversations/{cid}/references", response_model=ReferenceOut,
             status_code=status.HTTP_201_CREATED)
def add_reference(cid: int, payload: ReferenceCreate, request: Request,
                  db: Database = Depends(get_db)):
    _require_conv(db, cid)
    svc = ReferenceService(db)
    cw = _context_window(request)
    try:
        if payload.kind == "external":
            if not payload.source_path:
                raise ValueError("source_path required")
            return svc.add_external(cid, payload.source_path, cw)
        if payload.ingest_record_id is None:
            raise ValueError("ingest_record_id required")
        return svc.add_internal(cid, payload.ingest_record_id, cw)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.delete("/conversations/{cid}/references/{rid}", status_code=status.HTTP_204_NO_CONTENT)
def detach_reference(cid: int, rid: int, db: Database = Depends(get_db)):
    _require_conv(db, cid)
    ReferenceService(db).detach(cid, rid)
