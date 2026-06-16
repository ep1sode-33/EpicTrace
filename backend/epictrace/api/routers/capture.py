from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from epictrace.api.deps import (
    get_asr_provisioner,
    get_db,
    get_embedder,
    get_vector_store,
)
from epictrace.db import Database
from epictrace.schemas import (
    AppendEventIn,
    CaptureEventOut,
    CaptureSessionDetailOut,
    CaptureSessionOut,
    IndexStatusOut,
    OrganizeIn,
    PartialIn,
    RenameIn,
    StartSessionIn,
)
from epictrace.api.routers.projects import _ensure_project, _has_running_job
from epictrace.media.errors import ExtractionEngineNotReady, ExtractionFailed
from epictrace.services.capture import CaptureService
from epictrace.services.errors import (
    ActiveSessionExists,
    CaptureSessionNotFound,
    InvalidSourcePath,
    ProjectNotFound,
    SessionAlreadyOrganized,
    SessionNotRecording,
    SessionNotStaged,
    SourceFileNotFound,
)
from epictrace.services.index import IndexService
from epictrace.services.organize import OrganizeService

router = APIRouter(prefix="/capture", tags=["capture"])  # /api 由 app 工厂挂载

_log = logging.getLogger("epictrace")


def _detail(svc: CaptureService, sess) -> CaptureSessionDetailOut:
    return CaptureSessionDetailOut(
        id=sess.id, title=sess.title, status=sess.status,
        started_at=sess.started_at, ended_at=sess.ended_at, sources=sess.sources,
        staging_dir=sess.staging_dir,
        events=[CaptureEventOut.model_validate(e) for e in sess.events],
        elapsed_seconds=svc.active_elapsed_seconds(sess.id),
    )


_AUDIO_SOURCES = ("mic", "system_audio")


@router.post("/sessions", response_model=CaptureSessionOut, status_code=status.HTTP_201_CREATED)
def start_session(payload: StartSessionIn, request: Request,
                  db: Database = Depends(get_db)) -> CaptureSessionOut:
    # 服务端硬门:选了音频源(mic/system_audio)但语音模型未就绪 → 直接 409,绝不建 session。
    # 前端虽已挡(CaptureView),但直连 API / 陈旧页面 / 竞态仍可能漏进来 → worker 会 Popen 起
    # WhisperModel 阻塞下载 ~3GB(或挂死)→ 用户最怕的「静默漏录/卡死」。在源头守住(FIX 1)。
    if any(s in _AUDIO_SOURCES for s in payload.sources):
        cfg = _asr_settings(request)
        if not get_asr_provisioner(request).is_ready(cfg.get("model", "large-v3")):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="语音模型未就绪,请先在设置下载")
    try:
        sess = CaptureService(db).start(sources=payload.sources)
    except ActiveSessionExists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="a session is already recording")
    # 选了音频源 → 拉起 ASR worker 子进程(supervisor 内部判定 mic/system_audio 才真起)。
    # 任何失败只记日志、不挡 session(降级:其余源/事件照常,见 spec §12)。
    _start_asr(request, sess)
    return CaptureSessionOut.model_validate(sess)


def _asr_settings(request: Request) -> dict:
    """从持久化 ASR 设置取完整配置(model/vad/阈值/force_confirm_after…);读取失败回落默认。

    返回完整 dict(经 SettingsService.get_asr_settings → AsrConfig 规范化),透传给 supervisor,
    worker 据此建完整 AsrConfig —— 非默认调参全部生效(FIX D)。
    """
    try:
        from epictrace.services.settings import SettingsService
        return SettingsService(request.app.state.config).get_asr_settings()
    except Exception:  # noqa: BLE001 — 设置读不到不应挡 session
        from epictrace.asr.config import AsrConfig
        return AsrConfig().to_dict()


def _asr_cache_dir(request: Request) -> str | None:
    """ASR 模型缓存目录(WhisperModel download_root)。与 provisioner 就绪检测同一路径,
    透传给 worker → 非默认数据目录下二者不再各看各的(FIX 2)。取不到回 None(worker 落默认)。"""
    config = getattr(request.app.state, "config", None)
    if config is None:
        return None
    return str(config.asr_model_dir)


def _start_asr(request: Request, sess) -> None:
    sup = getattr(request.app.state, "asr_supervisor", None)
    if sup is None:
        return
    try:
        cfg = _asr_settings(request)
        sup.start(session_id=sess.id, sources=list(sess.sources),
                  staging_dir=sess.staging_dir, model=cfg.get("model", "large-v3"),
                  config=cfg, cache_dir=_asr_cache_dir(request))
    except Exception as e:  # noqa: BLE001 — 子进程拉起失败降级,不挡 session
        _log.warning("ASR supervisor.start failed for session %s: %s", sess.id, e)


def _stop_asr(request: Request, sid: int) -> None:
    sup = getattr(request.app.state, "asr_supervisor", None)
    if sup is None:
        return
    try:
        sup.stop(sid)
    except Exception as e:  # noqa: BLE001 — 停止尽力而为
        _log.warning("ASR supervisor.stop failed for session %s: %s", sid, e)


@router.get("/sessions", response_model=list[CaptureSessionOut])
def list_sessions(db: Database = Depends(get_db)) -> list[CaptureSessionOut]:
    return [CaptureSessionOut.model_validate(s) for s in CaptureService(db).list_sessions()]


@router.get("/sessions/active", response_model=CaptureSessionOut | None)
def active_session(db: Database = Depends(get_db)):
    sess = CaptureService(db).active_session()
    return CaptureSessionOut.model_validate(sess) if sess else None


@router.get("/sessions/{sid}", response_model=CaptureSessionDetailOut)
def get_session(sid: int, db: Database = Depends(get_db)) -> CaptureSessionDetailOut:
    svc = CaptureService(db)
    try:
        return _detail(svc, svc.get_session(sid))
    except CaptureSessionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")


@router.post("/sessions/{sid}/events", response_model=CaptureEventOut,
             status_code=status.HTTP_201_CREATED)
def append_event(sid: int, payload: AppendEventIn, db: Database = Depends(get_db)) -> CaptureEventOut:
    try:
        ev = CaptureService(db).append_event(sid, kind=payload.kind, payload=payload.payload,
                                             meta=payload.meta)
    except CaptureSessionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    except SessionNotRecording:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not recording")
    return CaptureEventOut.model_validate(ev)


@router.post("/sessions/{sid}/pause", status_code=status.HTTP_204_NO_CONTENT)
def pause(sid: int, request: Request, db: Database = Depends(get_db)) -> None:
    _pause_resume(db, sid, "pause")
    _asr_pause_resume(request, sid, "pause")


@router.post("/sessions/{sid}/resume", status_code=status.HTTP_204_NO_CONTENT)
def resume(sid: int, request: Request, db: Database = Depends(get_db)) -> None:
    _pause_resume(db, sid, "resume")
    _asr_pause_resume(request, sid, "resume")


def _pause_resume(db: Database, sid: int, which: str) -> None:
    svc = CaptureService(db)
    try:
        svc.pause(sid) if which == "pause" else svc.resume(sid)
    except CaptureSessionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    except SessionNotRecording:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not recording")


def _asr_pause_resume(request: Request, sid: int, which: str) -> None:
    """暂停/恢复联动 ASR worker(暂停停喂入,恢复重起;失败只记日志,不挡事件流)。"""
    sup = getattr(request.app.state, "asr_supervisor", None)
    if sup is None:
        return
    try:
        sup.pause(sid) if which == "pause" else sup.resume(sid)
    except Exception as e:  # noqa: BLE001
        _log.warning("ASR supervisor.%s failed for session %s: %s", which, sid, e)


@router.post("/sessions/{sid}/stop", response_model=CaptureSessionOut)
def stop_session(sid: int, request: Request, db: Database = Depends(get_db)) -> CaptureSessionOut:
    svc = CaptureService(db)
    # 先确认 session 存在(404),再停 ASR worker —— 必须在翻 staged 之前停(FIX B):否则
    # worker 收尾时的最后几个 confirmed POST 撞到 SessionNotRecording 409 被丢。worker 收
    # SIGTERM 后会 flush 最后 confirmed 段 + finalize wav,这些 POST 需 session 仍 recording。
    try:
        svc.get_session(sid)
    except CaptureSessionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    _stop_asr(request, sid)
    sess = svc.stop(sid)  # 此刻才翻 staged
    request.app.state.asr_partials.pop(sid, None)  # 清掉该 session 的内存态 partial
    return CaptureSessionOut.model_validate(sess)


@router.post("/sessions/{sid}/partial", status_code=status.HTTP_204_NO_CONTENT)
def post_partial(sid: int, payload: PartialIn, request: Request) -> None:
    """ASR worker 回推实时暂定段(不落库),存内存态 asr_partials[sid][source],经 SSE 推 HUD。"""
    partials = request.app.state.asr_partials
    partials.setdefault(sid, {})[payload.source] = payload.text


@router.get("/sessions/{sid}/partial")
def get_partial(sid: int, request: Request) -> dict:
    """读该 session 的实时暂定段快照({source: text})。供 HUD 现有 1.5s 轮询(不另起 SSE)
    与 getSession 一起拉取;内存态、无则空 dict。"""
    return dict(request.app.state.asr_partials.get(sid, {}))


@router.patch("/sessions/{sid}", response_model=CaptureSessionOut)
def rename_session(sid: int, payload: RenameIn, db: Database = Depends(get_db)) -> CaptureSessionOut:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="title must not be empty")
    try:
        return CaptureSessionOut.model_validate(CaptureService(db).rename(sid, title[:512]))
    except CaptureSessionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")


@router.delete("/sessions/{sid}", status_code=status.HTTP_200_OK)
def delete_session(sid: int, request: Request, db: Database = Depends(get_db)) -> dict:
    # 先停 ASR worker(若仍在跑),再删 session + staging(避免 worker 还往已删目录写 wav)。
    _stop_asr(request, sid)
    request.app.state.asr_partials.pop(sid, None)
    try:
        CaptureService(db).delete(sid)
    except CaptureSessionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    return {"deleted": True, "id": sid}


@router.post("/sessions/{sid}/organize", response_model=IndexStatusOut)
def organize_session(sid: int, payload: OrganizeIn, request: Request,
                     db: Database = Depends(get_db)) -> IndexStatusOut:
    """物化 + 入库(OrganizeService),然后复用项目索引后台 job(进度走现有 index/status 轮询)。"""
    pid = payload.project_id
    # 先校验目标项目存在(404),避免归类/入库时才以 500 暴出。
    _ensure_project(db, pid)
    svc = IndexService(db, get_embedder(request), lambda: get_vector_store(request))
    # 「检查在跑 + 归类入库 + 启动新 job」整段在锁内:与 /projects/{id}/index 同一套并发护栏,
    # 避免该项目已有索引 job 在跑时再归类起两个并发 job(破坏性)。
    with request.app.state.index_lock:
        if _has_running_job(request, pid):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="indexing already in progress for this project")
        try:
            OrganizeService(db).organize(session_id=sid, project_id=pid)
        except CaptureSessionNotFound:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
        except ProjectNotFound:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
        except SessionAlreadyOrganized:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="session already organized")
        except SessionNotStaged:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="session not staged (stop it before organizing)")
        except (SourceFileNotFound, InvalidSourcePath) as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except (ExtractionEngineNotReady, ExtractionFailed) as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
        # 入库后启动该项目的后台索引 job(与 /projects/{id}/index 同一套机制 + 锁 + 轮询)。
        job = svc.index_project(pid)
        request.app.state.index_jobs[pid] = job
        svc.run_in_background(job)
    with job._lock:
        return IndexStatusOut(project_id=job.project_id, total=job.total, done=job.done,
                              status=job.status, errors=list(job.errors))


@router.get("/sessions/{sid}/events/stream")
async def stream_events(sid: int, request: Request, db: Database = Depends(get_db)):
    """SSE live feed:轮询会话事件,新增则推。session 非 recording 时收尾。"""
    svc = CaptureService(db)

    async def gen():
        last = 0
        while True:
            if await request.is_disconnected():
                break
            try:
                sess = svc.get_session(sid)
            except CaptureSessionNotFound:
                break
            new = [e for e in sess.events if e.id > last]
            for e in new:
                last = e.id
                yield {"event": "event", "data": json.dumps(
                    {"id": e.id, "kind": e.kind, "payload": e.payload,
                     "ts": e.ts.isoformat(), "meta": e.meta})}
            # 每轮额外推一份 partial 快照(实时暂定段,内存态;前端单独渲染、不混进 events)。
            partial = request.app.state.asr_partials.get(sid, {})
            if partial:
                yield {"event": "partial", "data": json.dumps(partial)}
            if sess.status != "recording":
                yield {"event": "done", "data": "{}"}
                break
            await asyncio.sleep(1.0)

    return EventSourceResponse(gen())
