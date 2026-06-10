from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from epictrace.api.deps import get_db
from epictrace.db import Database
from epictrace.schemas import ProjectCreate, ProjectOut, ScanResultOut
from epictrace.services.projects import ProjectService
from epictrace.services.scan import ScanService

router = APIRouter(prefix="/projects", tags=["projects"])  # /api 由 app 工厂统一挂载


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Database = Depends(get_db)) -> ProjectOut:
    proj = ProjectService(db).create(title=payload.title, folder_path=payload.folder_path)
    return ProjectOut.model_validate(proj)


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Database = Depends(get_db)) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in ProjectService(db).list()]


@router.post("/{project_id}/scan", response_model=ScanResultOut)
def scan_project(project_id: int, db: Database = Depends(get_db)) -> ScanResultOut:
    try:
        result = ScanService(db).scan_and_register(project_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return ScanResultOut(added=result.added, missing=result.missing)
