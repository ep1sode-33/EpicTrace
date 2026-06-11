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
    # 后台线程会原地更新 job.done/errors/status,读时取锁拍快照。
    with job._lock:
        return IndexStatusOut(
            project_id=job.project_id,
            total=job.total,
            done=job.done,
            status=job.status,
            errors=list(job.errors),
        )


def _ensure_project(db: Database, project_id: int) -> None:
    from epictrace.models import Project

    with db.session() as s:
        if s.get(Project, project_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")


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
    _ensure_project(db, project_id)
    # vector store 传 getter 延迟构造:Milvus(gRPC)会在后台线程 warmup 模型之后才创建,
    # 避免 'gRPC 激活后再 fork 加载模型' 的段错误(见 services/index.py._run)。
    svc = IndexService(db, get_embedder(request), lambda: get_vector_store(request))
    # 构建 running 的 job 并在守护线程里推进 per-file 工作,立刻返回 running 状态。
    # (同步等待会在真模型上把请求拖到超时;前端改为轮询 status 读实时进度。)
    job = svc.index_project(project_id)
    request.app.state.index_jobs[project_id] = job
    svc.run_in_background(job)
    return _job_to_out(job)


@router.get("/{project_id}/index/status", response_model=IndexStatusOut)
def index_status(project_id: int, request: Request, db: Database = Depends(get_db)) -> IndexStatusOut:
    _ensure_project(db, project_id)
    job = request.app.state.index_jobs.get(project_id)
    if job is None:
        return IndexStatusOut(project_id=project_id, total=0, done=0, status="idle")
    return _job_to_out(job)
