from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from epictrace.api.deps import get_db
from epictrace.db import Database
from epictrace.media.errors import ExtractionEngineNotReady, ExtractionFailed
from epictrace.schemas import SourceOut
from epictrace.services.source import SourceService

router = APIRouter(tags=["source"])  # /api 由 app 工厂统一挂载


@router.get("/source/{ingest_record_id}", response_model=SourceOut)
def get_source(ingest_record_id: int, db: Database = Depends(get_db)):
    try:
        return SourceOut(**SourceService(db).get_text(ingest_record_id))
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    except ExtractionEngineNotReady as e:
        # 缓存缺失时的残留现提取路径才会触发;给干净 409 而非 500。
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except ExtractionFailed as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/attachment-source/{reference_id}", response_model=SourceOut)
def get_attachment_source(reference_id: int, db: Database = Depends(get_db)):
    try:
        return SourceOut(**SourceService(db).get_attachment_text(reference_id))
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    except ExtractionEngineNotReady as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except ExtractionFailed as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
