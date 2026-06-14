from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from epictrace.api.deps import get_db
from epictrace.db import Database
from epictrace.media.errors import ExtractionEngineNotReady, ExtractionFailed
from epictrace.schemas import IngestRecordOut, IngestRequest
from epictrace.services.errors import (
    InvalidSourcePath,
    ProjectNotFound,
    SourceFileNotFound,
    SourceUnreadable,
)
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
    except ProjectNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except SourceFileNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InvalidSourcePath as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except SourceUnreadable as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ExtractionEngineNotReady as e:
        # 富文档提取引擎(MinerU)未就绪:提示用户去设置里安装,前端可引导。
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ExtractionFailed as e:
        # MinerU 子进程失败/超时/缺输出:内容无法处理。沿用本项目对提取失败的 400 约定
        # (见 references 路由的 add_external 失败映射),且与 422 弃用项无关。
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return IngestRecordOut.model_validate(rec)


@router.get("", response_model=list[IngestRecordOut])
def list_files(project_id: int, db: Database = Depends(get_db)) -> list[IngestRecordOut]:
    return [
        IngestRecordOut.model_validate(r)
        for r in IngestService(db).list_for_project(project_id)
    ]
