from __future__ import annotations

import asyncio
import queue
import threading

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from epictrace.api.deps import get_db, get_embedder, get_attachment_store, get_provisioner
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


class _Lazy:
    """惰性代理:首次真正用到(.属性/调用)时才执行 factory 构造真件。
    用于把 embedder / attachment_store 传给 ReferenceService —— 小文件(fulltext)路径
    根本不碰它们,就不该急切起 BGE-M3 / 第二个 Milvus 客户端(否则与已有 Milvus gRPC
    在同进程内 fork 冲突段错误,见 macos-embedding-milvus-fork-order)。仅当大文件走 indexed
    分支(_index_attachment 调 .embed / .upsert)时才真正构造。"""

    def __init__(self, factory) -> None:
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_obj", None)

    def _resolve(self):
        obj = object.__getattribute__(self, "_obj")
        if obj is None:
            obj = object.__getattribute__(self, "_factory")()
            object.__setattr__(self, "_obj", obj)
        return obj

    def __getattr__(self, name):
        return getattr(self._resolve(), name)


@router.get("/conversations/{cid}/references", response_model=list[ReferenceOut])
def list_references(cid: int, db: Database = Depends(get_db)):
    _require_conv(db, cid)
    return ReferenceService(db).list_active(cid)


@router.post("/conversations/{cid}/references", response_model=ReferenceOut,
             status_code=status.HTTP_201_CREATED)
def add_reference(cid: int, payload: ReferenceCreate, request: Request,
                  db: Database = Depends(get_db)):
    _require_conv(db, cid)
    # 惰性构造 embedder / attachment_store:仅当外部大文件真正走 indexed 分支时才起真件,
    # 避免小文件(fulltext)用例急切加载 BGE-M3 / 第二个 Milvus 客户端。
    svc = ReferenceService(db, embedder=_Lazy(lambda: get_embedder(request)),
                           attachment_store=_Lazy(lambda: get_attachment_store(request)),
                           provisioner=get_provisioner(request))
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


_DONE = object()  # 哨兵:worker 线程结束(成功/失败)后放入队列,通知 SSE 生成器收尾。


@router.post("/conversations/{cid}/references/stream")
def add_reference_stream(cid: int, payload: ReferenceCreate, request: Request,
                         db: Database = Depends(get_db)):
    """挂附件并把提取进度实时 SSE 出去(语义仍是 block-until-ready:仅在 done 事件携带
    创建好的引用,前端据此刷新;附件只在提取完成后才可用,不是 fire-and-forget)。

    事件:
      {"event":"status","data":"解析中 12/29"}  — 提取进度(MinerU 逐条)
      {"event":"done",  "data": <ReferenceOut JSON>}  — 成功,带创建好的引用
      {"event":"error", "data": "<message>"}  — 提取/参数失败(ValueError/引擎未就绪等)

    阻塞提取跑在 worker 线程,progress_cb 把进度推进线程安全队列;SSE 生成器边消费
    队列边吐 status,从而「提取进行中就持续有进度」,而非等整个提取完成才一次性返回。
    仅支持 external(走 MinerU 等长耗时提取);internal/无源仍用非流式 add_reference。
    """
    _require_conv(db, cid)
    if payload.kind != "external" or not payload.source_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "stream attach requires kind=external with source_path")
    svc = ReferenceService(db, embedder=_Lazy(lambda: get_embedder(request)),
                           attachment_store=_Lazy(lambda: get_attachment_store(request)),
                           provisioner=get_provisioner(request))
    cw = _context_window(request)
    # 有界队列:客户端断开后生成器停止 drain,worker 仍可能短暂 put 进度——
    # 用 put_nowait 丢弃满时的进度(不阻塞 worker),保证 worker 在 cancel 后能尽快退出。
    q: queue.Queue = queue.Queue(maxsize=256)
    cancel = threading.Event()

    def _emit(msg: str) -> None:
        try:
            q.put_nowait(("status", msg))
        except queue.Full:
            pass  # 进度是可丢弃的瞬态;丢一条不影响最终 done/error

    def _work() -> None:
        # worker:跑阻塞提取,进度→队列;结束放 (_DONE, result_or_None, error_or_None)。
        # cancel(客户端断开时由生成器 set)透传到提取层,及时停掉 mineru 子进程。
        try:
            ref = svc.add_external(cid, payload.source_path, cw,
                                   progress_cb=_emit, cancel=cancel)
            _put_done(q, (_DONE, ref, None))
        except Exception as e:  # noqa: BLE001 — 任意失败 → 经队列转成 error 事件
            _put_done(q, (_DONE, None, str(e)))

    async def gen():
        worker = threading.Thread(target=_work, daemon=True)
        worker.start()
        try:
            while True:
                try:
                    kind, a, b = _unpack(q.get_nowait())
                except queue.Empty:
                    # 没有待发事件:检查客户端是否断开;断开则取消 worker 并收尾。
                    if await request.is_disconnected():
                        cancel.set()
                        return
                    await asyncio.sleep(0.05)
                    continue
                if kind == "status":
                    yield {"event": "status", "data": a}
                    continue
                # kind is _DONE: a=created ref dict (or None), b=error message (or None)
                if b is not None:
                    yield {"event": "error", "data": b}
                else:
                    yield {"event": "done",
                           "data": ReferenceOut.model_validate(a).model_dump_json()}
                return
        finally:
            # 正常结束或客户端中途断开(GeneratorExit)都置 cancel:让 worker 停掉
            # 并杀掉 mineru 子进程,不再空跑数分钟、不再无界堆积队列。
            cancel.set()

    return EventSourceResponse(gen())


def _unpack(item):
    """队列项规整为 (kind, a, b):status→("status", msg, None);完成→(_DONE, ref, err)。"""
    if item[0] == "status":
        return ("status", item[1], None)
    return item  # (_DONE, ref_or_None, err_or_None)


def _put_done(q: queue.Queue, item) -> None:
    """投递终态(done/error)。短超时阻塞:正常情况下消费者在持续 drain,队列必有空位;
    若客户端已断开(消费者停 drain、队列被进度占满),超时后丢弃即可——没有读者再关心终态,
    关键是不让 worker 永久阻塞在 put 上(否则线程泄漏)。"""
    try:
        q.put(item, timeout=1.0)
    except queue.Full:
        pass


@router.delete("/conversations/{cid}/references/{rid}", status_code=status.HTTP_204_NO_CONTENT)
def detach_reference(cid: int, rid: int, request: Request, db: Database = Depends(get_db)):
    _require_conv(db, cid)
    store = getattr(request.app.state, "attachment_store", None)
    ReferenceService(db, attachment_store=store).detach(cid, rid)
