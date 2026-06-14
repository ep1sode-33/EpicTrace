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


@router.delete("/{project_id}", status_code=status.HTTP_200_OK)
def delete_project(
    project_id: int,
    request: Request,
    delete_folder: bool = False,
    db: Database = Depends(get_db),
) -> dict:
    _ensure_project(db, project_id)

    # 仅当项目确有"已索引"记录(= 向量库里真有它的向量)时才碰 Milvus。否则没必要构造
    # Milvus —— 那会顺带 warmup 模型(见 deps.get_vector_store),让"删一个没索引过的项目"
    # 白白加载几 GB 模型。无已索引内容时直接跳过,保持删除瞬时。
    from sqlalchemy import func, select

    from epictrace.models import IngestRecord

    with db.session() as s:
        indexed_count = s.execute(
            select(func.count())
            .select_from(IngestRecord)
            .where(
                IngestRecord.project_id == project_id,
                IngestRecord.indexed.is_(True),
            )
        ).scalar_one()
    if indexed_count > 0:
        # 清理向量库(get_vector_store 会先 warmup 模型再起 Milvus,保证顺序安全)。
        # 失败不阻断 DB 删除,但记日志。
        try:
            get_vector_store(request).delete_by_project(project_id)
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("epictrace").warning(
                "删除项目 %s 的向量失败(不阻断删除): %s", project_id, exc
            )

    # 删 DB 行(ingest_records 经 cascade 一并删除),可选删盘上文件夹。
    folder_path = ProjectService(db).delete(project_id, delete_folder=delete_folder)
    # 该项目可能残留的索引任务状态一并丢弃,避免 status 轮询到旧 job。
    request.app.state.index_jobs.pop(project_id, None)
    return {"deleted": True, "project_id": project_id, "folder_path": folder_path}


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


@router.post("/{project_id}/reindex", response_model=IndexStatusOut)
def reindex_project(project_id: int, request: Request, db: Database = Depends(get_db)) -> IndexStatusOut:
    """用当前提取引擎重建该项目索引:清旧向量 + 把记录翻回待索引 + 重跑同一条索引流水线。
    与 index_project 同形返回(running 的 job),前端复用同一套 index/status 轮询读进度。"""
    _ensure_project(db, project_id)
    # 同 index_project:vector store 传 getter 延迟构造。注意 reindex_project 会在本请求线程里
    # 先 delete_by_project(同步清向量)——getter(get_vector_store)保证「先 warmup 模型再起
    # Milvus」,避免 'gRPC 激活后再 fork 加载模型' 段错误(见 deps.get_vector_store)。
    svc = IndexService(db, get_embedder(request), lambda: get_vector_store(request))
    job = svc.reindex_project(project_id)
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
