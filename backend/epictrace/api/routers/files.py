from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from epictrace.api.deps import get_db
from epictrace.db import Database
from epictrace.schemas import IngestRecordOut, IngestRequest
from epictrace.services.ingest import IngestService

router = APIRouter(prefix="/files", tags=["files"])  # /api 由 app 工厂统一挂载


@router.post("/ingest", response_model=IngestRecordOut, status_code=status.HTTP_201_CREATED)
def ingest_file(payload: IngestRequest, db: Database = Depends(get_db)) -> IngestRecordOut:
    try:
        rec = IngestService(db).ingest_file(
            project_id=payload.project_id,
            source_path=payload.source_path,
            ingest_method=payload.ingest_method,
            description=payload.description,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return IngestRecordOut.model_validate(rec)


@router.get("", response_model=list[IngestRecordOut])
def list_files(project_id: int, db: Database = Depends(get_db)) -> list[IngestRecordOut]:
    return [
        IngestRecordOut.model_validate(r)
        for r in IngestService(db).list_for_project(project_id)
    ]
