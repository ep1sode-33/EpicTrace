from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from epictrace.api.deps import get_db, get_embedder, get_vector_store
from epictrace.db import Database
from epictrace.schemas import IndexStatusOut, ProjectCreate, ProjectOut, ScanResultOut
from epictrace.services.index import IndexService
from epictrace.services.projects import ProjectService
from epictrace.services.scan import ScanService

router = APIRouter(prefix="/projects", tags=["projects"])  # /api 由 app 工厂统一挂载


def _job_to_out(job) -> IndexStatusOut:
    return IndexStatusOut(
        project_id=job.project_id,
        total=job.total,
        done=job.done,
        status=job.status,
        errors=job.errors,
    )


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


@router.post("/{project_id}/index", response_model=IndexStatusOut)
def index_project(project_id: int, request: Request, db: Database = Depends(get_db)) -> IndexStatusOut:
    from epictrace.models import Project

    with db.session() as s:
        if s.get(Project, project_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    svc = IndexService(db, get_embedder(request), get_vector_store(request))
    job = svc.index_project(project_id)
    request.app.state.index_jobs[project_id] = job
    return _job_to_out(job)


@router.get("/{project_id}/index/status", response_model=IndexStatusOut)
def index_status(project_id: int, request: Request) -> IndexStatusOut:
    job = request.app.state.index_jobs.get(project_id)
    if job is None:
        return IndexStatusOut(project_id=project_id, total=0, done=0, status="idle")
    return _job_to_out(job)
