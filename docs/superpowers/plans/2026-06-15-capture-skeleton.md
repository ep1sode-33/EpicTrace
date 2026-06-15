# 采集骨架(Plan 8)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通「主动开 session → 带时间戳采集(笔记/剪贴板/截图)→ 采集后暂存 → 图形时间线 → 手动指派到 Project → 入库 + 后台索引」的纵向骨架,mic/系统内录 ASR 用接口缝隔开延后。

**Architecture:** 后端(FastAPI)做数据权威:新增 `capture_sessions`/`capture_events` 两表 + `CaptureService`(会话/事件/暂停/暂存)+ `Organizer` 接口缝与 `PassthroughOrganizer`(手动指派 → 物化文件 → 复用 `IngestService.ingest_file` → 复用 `IndexService` 后台索引)。shell(`run.py`)做浏览器拿不到的原生能力(截图 / 全局热键 / 剪贴板轮询 / 录制 HUD 第二窗口),事件直接 POST 后端。前端新增采集视图 + 暂存/时间线 + HUD 路由。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy(SQLite,`Base.metadata.create_all`)/ pytest(fakes,无网络)/ pywebview(js_api + 多窗口 + PyObjC)/ React 19 + Vite + Tailwind v4 + shadcn。

**约定(全程):**
- 后端测试在 `backend/`,用 `./.venv/bin/pytest`(切勿裸 `python`);前端 `npm run build` 在 `frontend/`。
- git 身份已配好,**绝不** `git config`;commit trailer 用 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
- 测试全 fake(注入 clock / 直接 POST 构造原生事件 / 复用 `tests/fakes.FakeEmbedder` + 真 `MilvusLiteStore`);不碰真 `~/.cache`、不联网。
- 简体中文写注释/文档,代码/路径/标识符/commit body 用英文(见 [[docs-in-simplified-chinese]])。
- shell 原生四件 + 前端无自动化测试(同 `read_clipboard_files` 的既有处理):薄封装 + 手测;门禁 = `npm run build` 与人工测。
- 分支已在 `feat/plan-8-capture-skeleton`。**不要 merge**——实现+codex审+William 真机测后才合(见 [[merge-only-after-manual-test]])。

---

## 文件结构

**后端(新建)**
- `epictrace/services/capture.py` — `CaptureService`(会话生命周期 + 事件 + 暂停 + active-elapsed + 暂存目录)。
- `epictrace/services/organize.py` — `OrganizeService`(物化 + 入库)。
- `epictrace/interfaces/organizer.py` — `Organizer` 协议 + `OrganizationProposal` + `PassthroughOrganizer`。
- `epictrace/interfaces/audio.py` — `AudioSource` 协议(仅定义,延后实现)。
- `epictrace/interfaces/transcriber.py` — `Transcriber` 协议 + `NoopTranscriber`(仅定义)。
- `epictrace/api/routers/capture.py` — 采集 API。

**后端(修改)**
- `epictrace/models.py` — 加 `CaptureSession` / `CaptureEvent` 两表 + `IngestRecord.source_session_id`。
- `epictrace/services/ingest.py` — `ingest_file(... source_session_id=None)`。
- `epictrace/services/errors.py` — 加采集相关异常。
- `epictrace/schemas.py` — 采集相关 Pydantic 输出/输入。
- `epictrace/api/app.py` — 挂 capture router。

**shell(修改)**
- `shell/run.py` — `js_api`:`capture_screenshot` / `start_capture_monitors` / `stop_capture_monitors` / `show_recording_hud` / `hide_recording_hud`;剪贴板轮询 + 全局热键 + HUD 第二窗口。

**前端(新建/修改)**
- `frontend/src/lib/api.ts` — 采集 client 方法 + SSE。
- `frontend/src/views/CaptureView.tsx` — 替换占位:源开关 + 开始/进行中 session。
- `frontend/src/components/RecordingHud.tsx` — HUD 浮窗 UI(图示按钮)。
- `frontend/src/views/CaptureStagingView.tsx` — 采集后暂存区 + 时间线 + 指派到 Project。
- `frontend/src/main.tsx` — `?view=hud` 分流渲染 HUD。
- `frontend/src/App.tsx` — 采集 tab 接 CaptureView/Staging;organize 后跳「信息处理和入库」。
- `frontend/src/lib/native.ts`(新)— `window.pywebview.api` 采集相关封装(开发态降级)。

---

## Phase 1 — 后端数据模型 + 会话服务(TDD)

### Task 1: 数据模型 — capture_sessions / capture_events / source_session_id

**Files:**
- Modify: `backend/epictrace/models.py`
- Test: `backend/tests/test_models_capture.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_models_capture.py
from datetime import datetime, timezone
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import CaptureSession, CaptureEvent


def _db(tmp_path: Path) -> Database:
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    return db


def test_session_with_events_roundtrip_and_cascade(tmp_path: Path):
    db = _db(tmp_path)
    with db.session() as s:
        sess = CaptureSession(
            title="会话 @x", status="recording",
            staging_dir=str(tmp_path / "sessions" / "1"), sources=["note", "clipboard"],
        )
        s.add(sess)
        s.flush()
        sid = sess.id
        s.add(CaptureEvent(session_id=sid, kind="note", payload="hi",
                           ts=datetime(2026, 6, 15, tzinfo=timezone.utc), meta={}))

    with db.session() as s:
        sess = s.get(CaptureSession, sid)
        assert sess.sources == ["note", "clipboard"]
        assert len(sess.events) == 1
        assert sess.events[0].kind == "note"

    # cascade: 删 session 连带删 events
    with db.session() as s:
        s.delete(s.get(CaptureSession, sid))
    with db.session() as s:
        from sqlalchemy import select
        assert s.execute(select(CaptureEvent)).scalars().all() == []


def test_ingest_record_has_optional_source_session_id(tmp_path: Path):
    from epictrace.models import IngestRecord
    assert IngestRecord.source_session_id is not None  # 列存在
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && ./.venv/bin/pytest tests/test_models_capture.py -q`
Expected: FAIL（`ImportError: cannot import name 'CaptureSession'`)

- [ ] **Step 3: 实现 — 在 `models.py` 加表与列**

`models.py` 顶部 import 行加 `JSON`(与现有 `String, Text, ForeignKey` 同处):
```python
from sqlalchemy import JSON, ForeignKey, String, Text  # 按现有顺序合并,新增 JSON
```
文件末尾(`ConversationReference` 之后)追加:
```python
class CaptureSession(Base):
    __tablename__ = "capture_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="recording")  # recording|staged|organized
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
    staging_dir: Mapped[str] = mapped_column(String(1024))
    sources: Mapped[list] = mapped_column(JSON, default=list)

    events: Mapped[list["CaptureEvent"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="CaptureEvent.ts",
    )


class CaptureEvent(Base):
    __tablename__ = "capture_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("capture_sessions.id"))
    kind: Mapped[str] = mapped_column(String(32))  # note|clipboard|screenshot|pause|resume|audio
    ts: Mapped[datetime] = mapped_column(default=_utcnow)
    payload: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    session: Mapped["CaptureSession"] = relationship(back_populates="events")
```
在 `IngestRecord` 类体内(`created_at` 之后、`project` relationship 之前)加:
```python
    source_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("capture_sessions.id"), nullable=True, default=None
    )
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && ./.venv/bin/pytest tests/test_models_capture.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/models.py backend/tests/test_models_capture.py
git commit -m "feat(capture): add capture_sessions/capture_events models + ingest source_session_id"
```

### Task 2: 采集异常类型

**Files:**
- Modify: `backend/epictrace/services/errors.py`
- Test:(随 Task 3 一起断言)

- [ ] **Step 1: 实现 — 在 `errors.py` 末尾加异常**

先读 `errors.py` 看现有基类风格(各异常多为 `class X(Exception)` 或带基类);**照其现有模式**追加:
```python
class ActiveSessionExists(Exception):
    """已有一个 recording 中的 session,不允许再开(单一活动 session)。"""


class CaptureSessionNotFound(Exception):
    def __init__(self, session_id: int) -> None:
        super().__init__(f"capture session not found: {session_id}")
        self.session_id = session_id


class SessionNotRecording(Exception):
    """对一个非 recording 的 session 追加事件/暂停/继续。"""


class SessionAlreadyOrganized(Exception):
    """对已 organized 的 session 再次 organize。"""
```

- [ ] **Step 2: 提交**(与 Task 3 同批提交亦可)

```bash
git add backend/epictrace/services/errors.py
git commit -m "feat(capture): add capture service error types"
```

### Task 3: CaptureService — 会话生命周期 + 事件 + 暂停 + active-elapsed

**Files:**
- Create: `backend/epictrace/services/capture.py`
- Test: `backend/tests/test_capture_service.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_capture_service.py
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.services.capture import CaptureService
from epictrace.services.errors import (
    ActiveSessionExists,
    CaptureSessionNotFound,
    SessionNotRecording,
)


class FakeClock:
    """可推进的时钟:每次 now() 返回当前值;advance() 前进。"""
    def __init__(self, start: datetime) -> None:
        self._t = start

    def now(self) -> datetime:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t = self._t + timedelta(seconds=seconds)


def _svc(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    clock = FakeClock(datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc))
    return CaptureService(db, clock=clock.now), clock


def test_start_creates_recording_session_and_staging_dir(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    sess = svc.start(sources=["note", "screenshot"])
    assert sess.status == "recording"
    assert sess.sources == ["note", "screenshot"]
    assert Path(sess.staging_dir).is_dir()
    assert svc.active_session().id == sess.id


def test_single_active_session(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    svc.start(sources=["note"])
    with pytest.raises(ActiveSessionExists):
        svc.start(sources=["note"])


def test_append_event_uses_clock_and_orders_by_ts(tmp_path: Path):
    svc, clock = _svc(tmp_path)
    sess = svc.start(sources=["note"])
    e1 = svc.append_event(sess.id, kind="note", payload="first")
    clock.advance(5)
    e2 = svc.append_event(sess.id, kind="note", payload="second")
    assert e2.ts > e1.ts
    got = svc.get_session(sess.id)
    assert [e.payload for e in got.events] == ["first", "second"]


def test_append_to_missing_session_raises(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    with pytest.raises(CaptureSessionNotFound):
        svc.append_event(999, kind="note", payload="x")


def test_append_to_stopped_session_raises(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    sess = svc.start(sources=["note"])
    svc.stop(sess.id)
    with pytest.raises(SessionNotRecording):
        svc.append_event(sess.id, kind="note", payload="x")


def test_stop_sets_staged_and_ended_at(tmp_path: Path):
    svc, clock = _svc(tmp_path)
    sess = svc.start(sources=["note"])
    clock.advance(10)
    stopped = svc.stop(sess.id)
    assert stopped.status == "staged"
    assert stopped.ended_at is not None
    assert svc.active_session() is None


def test_active_elapsed_excludes_paused_intervals(tmp_path: Path):
    svc, clock = _svc(tmp_path)
    sess = svc.start(sources=["note"])  # t0
    clock.advance(10)                   # +10 active
    svc.pause(sess.id)                  # pause at t0+10
    clock.advance(30)                   # paused 30s (excluded)
    svc.resume(sess.id)                 # resume at t0+40
    clock.advance(5)                    # +5 active
    svc.stop(sess.id)                   # end at t0+45
    assert svc.active_elapsed_seconds(sess.id) == pytest.approx(15.0)


def test_rename_and_delete_removes_staging(tmp_path: Path):
    svc, _ = _svc(tmp_path)
    sess = svc.start(sources=["note"])
    staging = Path(sess.staging_dir)
    (staging / "a.png").write_bytes(b"x")
    svc.rename(sess.id, "新名字")
    assert svc.get_session(sess.id).title == "新名字"
    svc.delete(sess.id)
    assert not staging.exists()
    assert svc.list_sessions() == []
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && ./.venv/bin/pytest tests/test_capture_service.py -q`
Expected: FAIL（`ModuleNotFoundError: epictrace.services.capture`)

- [ ] **Step 3: 实现 `capture.py`**

```python
# backend/epictrace/services/capture.py
from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.models import CaptureEvent, CaptureSession
from epictrace.services.errors import (
    ActiveSessionExists,
    CaptureSessionNotFound,
    SessionNotRecording,
)


def _utcnow() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)


class CaptureService:
    """会话生命周期 + 带时间戳事件 + 暂停语义 + 暂存目录。clock 可注入便于测试。"""

    def __init__(self, db: Database, clock: Callable[[], datetime] = _utcnow) -> None:
        self._db = db
        self._clock = clock

    # —— 会话 ——
    def start(self, sources: list[str]) -> CaptureSession:
        with self._db.session() as s:
            active = s.execute(
                select(CaptureSession).where(CaptureSession.status == "recording")
            ).scalars().first()
            if active is not None:
                raise ActiveSessionExists()
            now = self._clock()
            sess = CaptureSession(
                title=f"会话 @{now.strftime('%Y-%m-%d %H:%M')}",
                status="recording", started_at=now, sources=list(sources),
                staging_dir="",  # 落库拿到 id 后再定
            )
            s.add(sess)
            s.flush()
            sess.staging_dir = str(self._db.config.data_dir / "sessions" / str(sess.id))
            Path(sess.staging_dir).mkdir(parents=True, exist_ok=True)
            s.flush()
            s.refresh(sess)
            s.expunge(sess)
            return sess

    def stop(self, session_id: int) -> CaptureSession:
        with self._db.session() as s:
            sess = self._require(s, session_id)
            sess.status = "staged"
            sess.ended_at = self._clock()
            s.flush()
            s.refresh(sess)
            s.expunge(sess)
            return sess

    def rename(self, session_id: int, title: str) -> CaptureSession:
        with self._db.session() as s:
            sess = self._require(s, session_id)
            sess.title = title
            s.flush()
            s.refresh(sess)
            s.expunge(sess)
            return sess

    def delete(self, session_id: int) -> None:
        with self._db.session() as s:
            sess = self._require(s, session_id)
            staging = sess.staging_dir
            s.delete(sess)
        if staging:
            shutil.rmtree(staging, ignore_errors=True)

    def active_session(self) -> CaptureSession | None:
        with self._db.session() as s:
            sess = s.execute(
                select(CaptureSession).where(CaptureSession.status == "recording")
            ).scalars().first()
            if sess is not None:
                s.expunge(sess)
            return sess

    def list_sessions(self) -> list[CaptureSession]:
        with self._db.session() as s:
            rows = s.execute(
                select(CaptureSession).order_by(CaptureSession.started_at.desc())
            ).scalars().all()
            for r in rows:
                s.expunge(r)
            return list(rows)

    def get_session(self, session_id: int) -> CaptureSession:
        with self._db.session() as s:
            sess = self._require(s, session_id)
            _ = sess.events  # 触发加载
            s.expunge_all()
            return sess

    # —— 事件 ——
    def append_event(self, session_id: int, kind: str, payload: str = "",
                     meta: dict | None = None) -> CaptureEvent:
        with self._db.session() as s:
            sess = self._require(s, session_id)
            if sess.status != "recording":
                raise SessionNotRecording()
            ev = CaptureEvent(session_id=session_id, kind=kind, ts=self._clock(),
                             payload=payload, meta=meta or {})
            s.add(ev)
            s.flush()
            s.refresh(ev)
            s.expunge(ev)
            return ev

    def pause(self, session_id: int) -> CaptureEvent:
        return self.append_event(session_id, kind="pause")

    def resume(self, session_id: int) -> CaptureEvent:
        return self.append_event(session_id, kind="resume")

    def active_elapsed_seconds(self, session_id: int) -> float:
        """实际录制时长 = 总时长减去所有 pause→resume 区间。"""
        with self._db.session() as s:
            sess = self._require(s, session_id)
            start = sess.started_at
            end = sess.ended_at or self._clock()
            marks = [e for e in sess.events if e.kind in ("pause", "resume")]
        total = 0.0
        cursor = start
        paused = False
        for e in marks:
            if e.kind == "pause" and not paused:
                total += (e.ts - cursor).total_seconds()
                paused = True
            elif e.kind == "resume" and paused:
                cursor = e.ts
                paused = False
        if not paused:
            total += (end - cursor).total_seconds()
        return total

    def _require(self, s, session_id: int) -> CaptureSession:
        sess = s.get(CaptureSession, session_id)
        if sess is None:
            raise CaptureSessionNotFound(session_id)
        return sess
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && ./.venv/bin/pytest tests/test_capture_service.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/services/capture.py backend/tests/test_capture_service.py
git commit -m "feat(capture): CaptureService — session lifecycle, events, pause, active-elapsed"
```

---

## Phase 2 — 接口缝 + 归类入库(TDD)

### Task 4: 接口缝 — AudioSource / Transcriber(仅定义)

**Files:**
- Create: `backend/epictrace/interfaces/audio.py`
- Create: `backend/epictrace/interfaces/transcriber.py`
- Test: `backend/tests/test_capture_interfaces.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_capture_interfaces.py
def test_audio_source_protocol_importable():
    from epictrace.interfaces.audio import AudioSource  # noqa: F401


def test_transcriber_noop_returns_empty(tmp_path):
    from epictrace.interfaces.transcriber import NoopTranscriber
    out = NoopTranscriber().transcribe(str(tmp_path / "x.wav"))
    assert out == []
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && ./.venv/bin/pytest tests/test_capture_interfaces.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# backend/epictrace/interfaces/audio.py
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AudioSource(Protocol):
    """采音接口缝(延后实现)。覆盖外录(麦克风)与内录(系统音频)两类来源——
    后续各落一个实现(mic plan / 系统内录 plan,见
    docs/reference/asr-streaming-tuning-notes.md §5)。本期不实现。"""

    def start(self, session_id: int) -> None: ...

    def stop(self) -> list[str]:
        """停止采集,返回落盘的音频文件绝对路径列表。"""
        ...
```
```python
# backend/epictrace/interfaces/transcriber.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start: float  # 秒
    end: float


@runtime_checkable
class Transcriber(Protocol):
    """ASR 接口缝(延后实现)。mic ASR plan 落 faster-whisper 实现
    (调参/幻觉过滤见 docs/reference/asr-streaming-tuning-notes.md)。"""

    def transcribe(self, audio_path: str) -> list[TranscriptSegment]: ...


class NoopTranscriber:
    """本期默认:不转写,返回空。"""

    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        return []
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && ./.venv/bin/pytest tests/test_capture_interfaces.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/interfaces/audio.py backend/epictrace/interfaces/transcriber.py backend/tests/test_capture_interfaces.py
git commit -m "feat(capture): AudioSource/Transcriber interface seams (deferred ASR)"
```

### Task 5: ingest_file 接受 source_session_id

**Files:**
- Modify: `backend/epictrace/services/ingest.py`
- Test: `backend/tests/test_ingest_service.py`(追加一个用例)

- [ ] **Step 1: 追加失败测试**

在 `tests/test_ingest_service.py` 末尾追加:
```python
def test_ingest_records_source_session_id(tmp_path: Path):
    db, proj = _setup(tmp_path)
    src = tmp_path / "note.md"
    src.write_text("hello", encoding="utf-8")
    rec = IngestService(db).ingest_file(
        project_id=proj.id, source_path=str(src),
        ingest_method="session", description="", source_session_id=42,
    )
    assert rec.ingest_method == "session"
    assert rec.source_session_id == 42
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && ./.venv/bin/pytest tests/test_ingest_service.py::test_ingest_records_source_session_id -q`
Expected: FAIL（`ingest_file() got an unexpected keyword argument 'source_session_id'`)

- [ ] **Step 3: 实现 — 改 `ingest_file` 签名与建记录**

`ingest.py` 的 `ingest_file` 签名改为:
```python
    def ingest_file(
        self, project_id: int, source_path: str, ingest_method: str, description: str,
        source_session_id: int | None = None,
    ) -> IngestRecord:
```
构造 `IngestRecord(...)` 处加一行 `source_session_id=source_session_id,`(放在 `description=description,` 之后)。

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && ./.venv/bin/pytest tests/test_ingest_service.py -q`
Expected: PASS（含新用例,旧用例不破)

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/services/ingest.py backend/tests/test_ingest_service.py
git commit -m "feat(capture): ingest_file accepts source_session_id"
```

### Task 6: Organizer 接口缝 + PassthroughOrganizer

**Files:**
- Create: `backend/epictrace/interfaces/organizer.py`
- Test: `backend/tests/test_organizer.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_organizer.py
from datetime import datetime, timezone

from epictrace.interfaces.organizer import OrganizationProposal, PassthroughOrganizer
from epictrace.models import CaptureEvent, CaptureSession


def _evt(kind, payload, sec):
    return CaptureEvent(kind=kind, payload=payload,
                        ts=datetime(2026, 6, 15, 12, 0, sec, tzinfo=timezone.utc), meta={})


def test_passthrough_groups_text_into_markdown_and_lists_screenshots():
    sess = CaptureSession(id=7, title="S", status="staged",
                          staging_dir="/tmp/s/7", sources=["note", "screenshot"])
    events = [
        _evt("note", "想法一", 1),
        _evt("clipboard", "复制的链接", 2),
        _evt("note", "想法二", 3),
        _evt("screenshot", "shot-1.png", 4),
        _evt("pause", "", 5),  # 控制事件不进物化
    ]
    proposal = PassthroughOrganizer().propose(sess, events, hint_project_id=3)
    assert isinstance(proposal, OrganizationProposal)
    assert proposal.project_id == 3
    names = {name for name, _ in proposal.markdown_docs}
    assert names == {"notes.md", "clipboard.md"}
    notes = dict(proposal.markdown_docs)["notes.md"]
    assert "想法一" in notes and "想法二" in notes
    assert proposal.screenshot_rel_paths == ["shot-1.png"]


def test_passthrough_empty_session_yields_no_docs():
    sess = CaptureSession(id=8, title="S", status="staged", staging_dir="/tmp/s/8", sources=[])
    proposal = PassthroughOrganizer().propose(sess, [], hint_project_id=1)
    assert proposal.markdown_docs == []
    assert proposal.screenshot_rel_paths == []
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && ./.venv/bin/pytest tests/test_organizer.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# backend/epictrace/interfaces/organizer.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class OrganizationProposal:
    """归类提议(物化 + 入库的输入)。本期为直通形态:整段归一个 Project。
    后续真·归类 Agent 返回更丰富的提议(多 Project / 子文件夹 / 派生文件),execute 侧扩展即可。"""
    project_id: int
    markdown_docs: list[tuple[str, str]] = field(default_factory=list)  # (filename, content)
    screenshot_rel_paths: list[str] = field(default_factory=list)        # 相对 staging_dir


@runtime_checkable
class Organizer(Protocol):
    def propose(self, session, events, hint_project_id: int) -> OrganizationProposal: ...


class PassthroughOrganizer:
    """直通:笔记/剪贴板文本各合成一个 .md,截图列出文件名,全归到 hint_project_id。"""

    def propose(self, session, events, hint_project_id: int) -> OrganizationProposal:
        notes = [e.payload for e in events if e.kind == "note"]
        clips = [e.payload for e in events if e.kind == "clipboard"]
        shots = [e.payload for e in events if e.kind == "screenshot"]
        docs: list[tuple[str, str]] = []
        if notes:
            docs.append(("notes.md", "# 笔记\n\n" + "\n\n".join(notes) + "\n"))
        if clips:
            docs.append(("clipboard.md", "# 剪贴板\n\n" + "\n\n".join(clips) + "\n"))
        return OrganizationProposal(
            project_id=hint_project_id, markdown_docs=docs, screenshot_rel_paths=shots,
        )
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && ./.venv/bin/pytest tests/test_organizer.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/interfaces/organizer.py backend/tests/test_organizer.py
git commit -m "feat(capture): Organizer seam + PassthroughOrganizer"
```

### Task 7: OrganizeService — 物化 + 入库 + 标记 organized

**Files:**
- Create: `backend/epictrace/services/organize.py`
- Test: `backend/tests/test_organize_service.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_organize_service.py
from datetime import datetime, timezone
from pathlib import Path

import pytest

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import CaptureEvent, CaptureSession, IngestRecord
from epictrace.services.errors import SessionAlreadyOrganized
from epictrace.services.organize import OrganizeService
from epictrace.services.projects import ProjectService


def _setup(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    staging = tmp_path / "sessions" / "1"
    staging.mkdir(parents=True)
    (staging / "shot-1.png").write_bytes(b"\x89PNG")
    with db.session() as s:
        sess = CaptureSession(id=1, title="S", status="staged",
                              staging_dir=str(staging), sources=["note", "screenshot"])
        s.add(sess)
        s.add(CaptureEvent(session_id=1, kind="note", payload="virtual memory",
                          ts=datetime(2026, 6, 15, tzinfo=timezone.utc), meta={}))
        s.add(CaptureEvent(session_id=1, kind="screenshot", payload="shot-1.png",
                          ts=datetime(2026, 6, 15, 0, 0, 1, tzinfo=timezone.utc), meta={}))
    return db, proj


def test_organize_materializes_ingests_and_marks_organized(tmp_path: Path):
    db, proj = _setup(tmp_path)
    recs = OrganizeService(db).organize(session_id=1, project_id=proj.id)

    # 文本事件 → notes.md 入库,提取文本含原文;截图 → 图片落库,extracted_text 为空
    by_name = {Path(r.stored_path).name: r for r in recs}
    assert any(n.startswith("notes") and n.endswith(".md") for n in by_name)
    notes_rec = next(r for n, r in by_name.items() if n.startswith("notes"))
    assert "virtual memory" in notes_rec.extracted_text
    assert notes_rec.ingest_method == "session"
    assert notes_rec.source_session_id == 1
    shot_rec = next(r for n, r in by_name.items() if n.startswith("shot-1"))
    assert shot_rec.extracted_text == ""
    # 入库文件落在 Project 文件夹
    assert Path(notes_rec.stored_path).parent == Path(proj.folder_path)

    with db.session() as s:
        assert s.get(CaptureSession, 1).status == "organized"
        assert s.query(IngestRecord).count() == 2


def test_organize_twice_raises(tmp_path: Path):
    db, proj = _setup(tmp_path)
    OrganizeService(db).organize(session_id=1, project_id=proj.id)
    with pytest.raises(SessionAlreadyOrganized):
        OrganizeService(db).organize(session_id=1, project_id=proj.id)
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && ./.venv/bin/pytest tests/test_organize_service.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

```python
# backend/epictrace/services/organize.py
from __future__ import annotations

from pathlib import Path

from epictrace.db import Database
from epictrace.interfaces.organizer import Organizer, PassthroughOrganizer
from epictrace.models import CaptureSession, IngestRecord
from epictrace.services.errors import (
    CaptureSessionNotFound,
    SessionAlreadyOrganized,
)
from epictrace.services.ingest import IngestService


class OrganizeService:
    """把一个 staged session 物化进 Project 文件夹并入库(复用 IngestService)。
    organizer 默认 PassthroughOrganizer(整段归一个 Project);真·归类 Agent 后续替换。"""

    def __init__(self, db: Database, organizer: Organizer | None = None) -> None:
        self._db = db
        self._organizer = organizer or PassthroughOrganizer()
        self._ingest = IngestService(db)

    def organize(self, session_id: int, project_id: int) -> list[IngestRecord]:
        with self._db.session() as s:
            sess = s.get(CaptureSession, session_id)
            if sess is None:
                raise CaptureSessionNotFound(session_id)
            if sess.status == "organized":
                raise SessionAlreadyOrganized()
            staging = Path(sess.staging_dir)
            events = list(sess.events)
            s.expunge_all()

        proposal = self._organizer.propose(sess, events, hint_project_id=project_id)

        records: list[IngestRecord] = []
        # 1) 物化 markdown 到 staging,再 ingest 进 Project
        for filename, content in proposal.markdown_docs:
            doc = staging / filename
            doc.write_text(content, encoding="utf-8")
            records.append(self._ingest.ingest_file(
                project_id=project_id, source_path=str(doc),
                ingest_method="session", description=f"采集自 session {session_id}",
                source_session_id=session_id,
            ))
        # 2) 截图文件 ingest 进 Project(本期无图像处理器 → extracted_text 为空)
        for rel in proposal.screenshot_rel_paths:
            shot = staging / rel
            if shot.is_file():
                records.append(self._ingest.ingest_file(
                    project_id=project_id, source_path=str(shot),
                    ingest_method="session", description=f"采集自 session {session_id}",
                    source_session_id=session_id,
                ))
        # 3) 标记 organized
        with self._db.session() as s:
            s.get(CaptureSession, session_id).status = "organized"
        return records
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && ./.venv/bin/pytest tests/test_organize_service.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/services/organize.py backend/tests/test_organize_service.py
git commit -m "feat(capture): OrganizeService materializes + ingests a session into a Project"
```

---

## Phase 3 — 采集 API(TDD)

### Task 8: schemas — 采集输出/输入

**Files:**
- Modify: `backend/epictrace/schemas.py`
- Test:(随 Task 9 路由测试覆盖)

- [ ] **Step 1: 实现 — 在 `schemas.py` 末尾追加**

先看文件顶部 import(应有 `from pydantic import BaseModel, ConfigDict`、`from datetime import datetime`;缺则补)。追加:
```python
class CaptureEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    ts: datetime
    payload: str
    meta: dict


class CaptureSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    sources: list[str]
    staging_dir: str  # 内部路径:前端不展示,仅转发给 native.startMonitors 供 shell 存截图


class CaptureSessionDetailOut(CaptureSessionOut):
    events: list[CaptureEventOut] = []
    elapsed_seconds: float = 0.0


class StartSessionIn(BaseModel):
    sources: list[str]


class AppendEventIn(BaseModel):
    kind: str
    payload: str = ""
    meta: dict = {}


class OrganizeIn(BaseModel):
    project_id: int
```

- [ ] **Step 2: 提交**

```bash
git add backend/epictrace/schemas.py
git commit -m "feat(capture): pydantic schemas for capture sessions/events"
```

### Task 9: 采集 router(CRUD + 事件 + 暂停 + SSE + organize→索引)

**Files:**
- Create: `backend/epictrace/api/routers/capture.py`
- Modify: `backend/epictrace/api/app.py`
- Test: `backend/tests/test_api_capture.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_api_capture.py
from pathlib import Path

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.vectorstore.milvus_lite import MilvusLiteStore
from fastapi.testclient import TestClient
from tests.fakes import FakeEmbedder


def _client(tmp_path: Path) -> TestClient:
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    store = MilvusLiteStore(db_path=str(tmp_path / "v.db"), dim=1024)
    return TestClient(create_app(db=db, embedder=FakeEmbedder(), vector_store=store))


def test_session_lifecycle_and_events(tmp_path: Path):
    c = _client(tmp_path)
    r = c.post("/api/capture/sessions", json={"sources": ["note"]})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["status"] == "recording"

    # 单一活动 session
    assert c.post("/api/capture/sessions", json={"sources": ["note"]}).status_code == 409

    assert c.post(f"/api/capture/sessions/{sid}/events",
                  json={"kind": "note", "payload": "hi"}).status_code == 201
    c.post(f"/api/capture/sessions/{sid}/pause")
    c.post(f"/api/capture/sessions/{sid}/resume")
    assert c.post(f"/api/capture/sessions/{sid}/stop").json()["status"] == "staged"

    detail = c.get(f"/api/capture/sessions/{sid}").json()
    kinds = [e["kind"] for e in detail["events"]]
    assert kinds == ["note", "pause", "resume"]


def test_rename_and_delete(tmp_path: Path):
    c = _client(tmp_path)
    sid = c.post("/api/capture/sessions", json={"sources": ["note"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/stop")
    assert c.patch(f"/api/capture/sessions/{sid}", json={"title": "新名"}).json()["title"] == "新名"
    assert c.delete(f"/api/capture/sessions/{sid}").status_code == 200
    assert c.get(f"/api/capture/sessions/{sid}").status_code == 404


def test_organize_ingests_and_starts_index_job(tmp_path: Path):
    c = _client(tmp_path)
    proj = c.post("/api/projects", json={"title": "P", "folder_path": str(tmp_path / "P")}).json()
    sid = c.post("/api/capture/sessions", json={"sources": ["note"]}).json()["id"]
    c.post(f"/api/capture/sessions/{sid}/events", json={"kind": "note", "payload": "virtual memory"})
    c.post(f"/api/capture/sessions/{sid}/stop")

    r = c.post(f"/api/capture/sessions/{sid}/organize", json={"project_id": proj["id"]})
    assert r.status_code == 200
    assert r.json()["project_id"] == proj["id"]      # 返回 IndexStatusOut(后台 job)
    # session 已 organized;项目里出现 1 条 session 入库记录
    assert c.get(f"/api/capture/sessions/{sid}").json()["status"] == "organized"
    files = c.get(f"/api/files?project_id={proj['id']}").json()
    assert any(f["ingest_method"] == "session" for f in files)

    # 再 organize → 409
    assert c.post(f"/api/capture/sessions/{sid}/organize",
                  json={"project_id": proj["id"]}).status_code == 409
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && ./.venv/bin/pytest tests/test_api_capture.py -q`
Expected: FAIL（404 — 路由未挂载)

- [ ] **Step 3: 实现 router**

```python
# backend/epictrace/api/routers/capture.py
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from epictrace.api.deps import get_db, get_embedder, get_vector_store
from epictrace.db import Database
from epictrace.schemas import (
    AppendEventIn,
    CaptureEventOut,
    CaptureSessionDetailOut,
    CaptureSessionOut,
    IndexStatusOut,
    OrganizeIn,
    RenameIn,
    StartSessionIn,
)
from epictrace.services.capture import CaptureService
from epictrace.services.errors import (
    ActiveSessionExists,
    CaptureSessionNotFound,
    SessionAlreadyOrganized,
    SessionNotRecording,
)
from epictrace.services.index import IndexService
from epictrace.services.organize import OrganizeService

router = APIRouter(prefix="/capture", tags=["capture"])  # /api 由 app 工厂挂载


def _detail(svc: CaptureService, sess) -> CaptureSessionDetailOut:
    return CaptureSessionDetailOut(
        id=sess.id, title=sess.title, status=sess.status,
        started_at=sess.started_at, ended_at=sess.ended_at, sources=sess.sources,
        events=[CaptureEventOut.model_validate(e) for e in sess.events],
        elapsed_seconds=svc.active_elapsed_seconds(sess.id),
    )


@router.post("/sessions", response_model=CaptureSessionOut, status_code=status.HTTP_201_CREATED)
def start_session(payload: StartSessionIn, db: Database = Depends(get_db)) -> CaptureSessionOut:
    try:
        sess = CaptureService(db).start(sources=payload.sources)
    except ActiveSessionExists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="a session is already recording")
    return CaptureSessionOut.model_validate(sess)


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
def pause(sid: int, db: Database = Depends(get_db)) -> None:
    _pause_resume(db, sid, "pause")


@router.post("/sessions/{sid}/resume", status_code=status.HTTP_204_NO_CONTENT)
def resume(sid: int, db: Database = Depends(get_db)) -> None:
    _pause_resume(db, sid, "resume")


def _pause_resume(db: Database, sid: int, which: str) -> None:
    svc = CaptureService(db)
    try:
        svc.pause(sid) if which == "pause" else svc.resume(sid)
    except CaptureSessionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    except SessionNotRecording:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session not recording")


@router.post("/sessions/{sid}/stop", response_model=CaptureSessionOut)
def stop_session(sid: int, db: Database = Depends(get_db)) -> CaptureSessionOut:
    try:
        return CaptureSessionOut.model_validate(CaptureService(db).stop(sid))
    except CaptureSessionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")


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
def delete_session(sid: int, db: Database = Depends(get_db)) -> dict:
    try:
        CaptureService(db).delete(sid)
    except CaptureSessionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    return {"deleted": True, "id": sid}


@router.post("/sessions/{sid}/organize", response_model=IndexStatusOut)
def organize_session(sid: int, payload: OrganizeIn, request: Request,
                     db: Database = Depends(get_db)) -> IndexStatusOut:
    """物化 + 入库(OrganizeService),然后复用项目索引后台 job(进度走现有 index/status 轮询)。"""
    try:
        OrganizeService(db).organize(session_id=sid, project_id=payload.project_id)
    except CaptureSessionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    except SessionAlreadyOrganized:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="session already organized")
    # 入库后启动该项目的后台索引 job(与 /projects/{id}/index 同一套机制 + 锁 + 轮询)。
    svc = IndexService(db, get_embedder(request), lambda: get_vector_store(request))
    with request.app.state.index_lock:
        job = svc.index_project(payload.project_id)
        request.app.state.index_jobs[payload.project_id] = job
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
            if sess.status != "recording":
                yield {"event": "done", "data": "{}"}
                break
            await asyncio.sleep(1.0)

    return EventSourceResponse(gen())
```
说明:`RenameIn` / `IndexStatusOut` 已存在于 `schemas.py`(projects 路由在用),直接复用。`sse_starlette` 已是依赖(chat/attach SSE 在用)。

- [ ] **Step 4: 在 `app.py` 挂载 router**

`app.py` 顶部 import 的 routers 元组里加 `capture`:
```python
from epictrace.api.routers import (
    capture,
    conversations,
    files,
    health,
    projects,
    references,
    settings,
    source,
)
```
在 `app.include_router(references.router, prefix="/api")` 之后加:
```python
    app.include_router(capture.router, prefix="/api")
```

- [ ] **Step 5: 跑测试,确认通过**

Run: `cd backend && ./.venv/bin/pytest tests/test_api_capture.py -q`
Expected: PASS（3 passed)

- [ ] **Step 6: 跑全后端套件防回归**

Run: `cd backend && ./.venv/bin/pytest -q`
Expected: 全绿(在 Task 1-9 累计基础上,旧用例不破)

- [ ] **Step 7: 提交**

```bash
git add backend/epictrace/api/routers/capture.py backend/epictrace/api/app.py backend/tests/test_api_capture.py
git commit -m "feat(capture): capture API (sessions/events/pause/stream/organize)"
```

---

## Phase 4 — shell 原生(手测,无自动化)

> 这四件是 pywebview 原生能力,**无自动化测试**(同 `read_clipboard_files` 的既有处理):薄封装 + 异常降级为空/日志 + 人工测。每步实现后 `cd shell && python -c "import ast,sys; ast.parse(open('run.py').read())"` 做语法自检,真正验证在 Phase 5 整体手测。

### Task 10: js_api 截图 `capture_screenshot`

**Files:**
- Modify: `shell/run.py`

- [ ] **Step 1: 实现 — `Api` 类加截图 + 事件上报**

在 `Api` 类内(`read_clipboard_files` 之后)加。`capture_screenshot()` **无参**:Quartz 抓全屏存 PNG 进**当前活动 session 的 staging_dir**(由 `start_capture_monitors` 存进 `self._cap`,见 Task 11),并由 shell **自行 POST** 截图事件——这样全局热键在前端失焦时也能采。`_post_event` 是截图与剪贴板**共用**的事件上报(放这里,Task 11 直接用)。
```python
    def capture_screenshot(self) -> str | None:
        """抓全屏存 PNG 进当前 session 的 staging_dir,POST screenshot 事件,返回文件名;失败→None。
        需「屏幕录制」权限;无活动监听(_cap 未设)或抓屏被拒 → None。"""
        cap = getattr(self, "_cap", None)
        if not cap:
            return None
        try:
            import time
            import Quartz
            from AppKit import NSBitmapImageRep, NSBitmapImageFileTypePNG

            image = Quartz.CGWindowListCreateImage(
                Quartz.CGRectInfinite, Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID, Quartz.kCGWindowImageDefault)
            if image is None:
                return None
            rep = NSBitmapImageRep.alloc().initWithCGImage_(image)
            png = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
            name = f"shot-{int(time.time() * 1000)}.png"
            out = Path(cap["dir"]) / name
            out.parent.mkdir(parents=True, exist_ok=True)
            png.writeToFile_atomically_(str(out), True)
            self._post_event(cap["sid"], "screenshot", name)
            return name
        except Exception as e:  # noqa: BLE001 — 抓屏任何异常降级
            print(f"[EpicTrace] capture_screenshot failed: {e}", flush=True)
            return None

    def _post_event(self, session_id: int, kind: str, payload: str) -> None:
        """shell 把采到的事件直接 POST 给后端(截图/剪贴板共用);失败重试一次后记日志。"""
        import urllib.request

        body = json.dumps({"kind": kind, "payload": payload}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:8765/api/capture/sessions/{session_id}/events",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        for attempt in (1, 2):
            try:
                urllib.request.urlopen(req, timeout=3)
                return
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    print(f"[EpicTrace] post event failed ({kind}): {e}", flush=True)
```
(`from pathlib import Path` 已在 `run.py` 顶部;`json` 已 import。`self._cap` 由 Task 11 的 `start_capture_monitors` 设置。)

- [ ] **Step 2: 语法自检 + 提交**

```bash
cd shell && python -c "import ast; ast.parse(open('run.py').read())" && cd ..
git add shell/run.py
git commit -m "feat(capture): js_api capture_screenshot (Quartz full-screen PNG)"
```

### Task 11: js_api 采集监听(剪贴板轮询 + 全局热键)

**Files:**
- Modify: `shell/run.py`

- [ ] **Step 1: 实现 — 监听器封装**

在 `Api` 类加 `start_capture_monitors(session_id, staging_dir, sources)` / `stop_capture_monitors()`。剪贴板:定时器轮询 `NSPasteboard.changeCount`,变化读文本去重 → POST `…/events`。全局热键:`NSEvent.addGlobalMonitorForEventsMatchingMask_handler_`(需辅助功能权限)监听设定组合键 → 截图 + POST。POST 用标准库 `urllib.request`(后端在 `127.0.0.1:8765`)。暂停态由 `stop_capture_monitors` 停、`start_capture_monitors` 起。
```python
    def start_capture_monitors(self, session_id: int, staging_dir: str, sources: list) -> dict:
        """按所选源起原生监听(剪贴板轮询 + 全局热键触发截图)。重复调用先停旧的。"""
        self.stop_capture_monitors()
        self._cap = {"sid": session_id, "dir": staging_dir,
                     "last_clip": None, "stop": False}
        try:
            from AppKit import NSPasteboard
            import threading

            pb = NSPasteboard.generalPasteboard()
            self._cap["clip_count"] = pb.changeCount()

            def _poll():
                while not self._cap.get("stop"):
                    try:
                        cnt = pb.changeCount()
                        if "clipboard" in sources and cnt != self._cap["clip_count"]:
                            self._cap["clip_count"] = cnt
                            txt = pb.stringForType_("public.utf8-plain-text")
                            if txt and txt != self._cap["last_clip"]:
                                self._cap["last_clip"] = txt
                                self._post_event(session_id, "clipboard", str(txt))
                    except Exception as e:  # noqa: BLE001
                        print(f"[EpicTrace] clipboard poll: {e}", flush=True)
                    import time
                    time.sleep(1.0)

            t = threading.Thread(target=_poll, daemon=True)
            t.start()
            self._cap["thread"] = t
            return {"ok": True}
        except Exception as e:  # noqa: BLE001 — 原生不可用降级
            print(f"[EpicTrace] start_capture_monitors failed: {e}", flush=True)
            return {"ok": False, "reason": str(e)}

    def stop_capture_monitors(self) -> None:
        cap = getattr(self, "_cap", None)
        if cap:
            cap["stop"] = True
        self._cap = None
```
(`_post_event` 已在 Task 10 定义,此处直接用 `self._post_event`。)
> 全局热键(`NSEvent.addGlobalMonitorForEventsMatchingMask_handler_` 监听 keyDown,匹配如 ⌘⇧2 → 调 `self.capture_screenshot()`,它内部已存图并 POST 截图事件)在本任务一并加;若 PyObjC 的全局监听在当前 pywebview 主循环不便挂载,降级为「仅 HUD/应用内按钮触发截图」并 `print` 告警(Phase 5 手测确认行为)。

- [ ] **Step 2: 语法自检 + 提交**

```bash
cd shell && python -c "import ast; ast.parse(open('run.py').read())" && cd ..
git add shell/run.py
git commit -m "feat(capture): native clipboard polling + global hotkey monitors"
```

### Task 12: 录制 HUD 第二窗口

**Files:**
- Modify: `shell/run.py`

- [ ] **Step 1: 实现 — HUD 窗口创建/销毁**

```python
    def show_recording_hud(self, session_id: int) -> dict:
        """开第二个无边框、置顶、可拖动的小窗口渲染 HUD(指向前端 ?view=hud 路由)。"""
        try:
            self._hud = webview.create_window(
                "EpicTrace 录制",
                f"http://127.0.0.1:8765/?view=hud&session={session_id}",
                frameless=True, on_top=True, easy_drag=True, resizable=False,
                width=360, height=52, x=40, y=40,
            )
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            print(f"[EpicTrace] show_recording_hud failed: {e}", flush=True)
            return {"ok": False, "reason": str(e)}

    def hide_recording_hud(self) -> None:
        hud = getattr(self, "_hud", None)
        if hud is not None:
            try:
                hud.destroy()
            except Exception as e:  # noqa: BLE001
                print(f"[EpicTrace] hide_recording_hud failed: {e}", flush=True)
        self._hud = None
```

- [ ] **Step 2: 语法自检 + 提交**

```bash
cd shell && python -c "import ast; ast.parse(open('run.py').read())" && cd ..
git add shell/run.py
git commit -m "feat(capture): recording HUD as a frameless on-top second window"
```

---

## Phase 5 — 前端(门禁 = `npm run build`,行为人工测)

> 前端无单测;每个任务实现后 `cd frontend && npm run build`(exit 0),整体行为在桌面 app 内人工测。组件遵循现有 shadcn/Tailwind 风格(看 `ProjectsConversationView.tsx` / `ProcessIngestView.tsx`)。

### Task 13: api.ts 采集 client + native 封装

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/native.ts`

- [ ] **Step 1: 实现 — `api.ts` 加类型与方法**

在 `api` 对象内加(仿现有 `fetch(...).then(j<T>)` 风格):
```typescript
// 类型(放文件类型区)
export type CaptureEvent = { id: number; kind: string; ts: string; payload: string; meta: Record<string, unknown> };
export type CaptureSession = { id: number; title: string; status: string; started_at: string; ended_at: string | null; sources: string[]; staging_dir: string };
export type CaptureSessionDetail = CaptureSession & { events: CaptureEvent[]; elapsed_seconds: number };

// api 对象内方法
startSession: (sources: string[]) =>
  fetch(`${BASE}/api/capture/sessions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sources }) }).then(j<CaptureSession>),
listSessions: () => fetch(`${BASE}/api/capture/sessions`).then(j<CaptureSession[]>),
activeSession: () => fetch(`${BASE}/api/capture/sessions/active`).then(j<CaptureSession | null>),
getSession: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}`).then(j<CaptureSessionDetail>),
appendEvent: (sid: number, kind: string, payload = "") =>
  fetch(`${BASE}/api/capture/sessions/${sid}/events`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, payload }) }).then(j<CaptureEvent>),
pauseSession: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}/pause`, { method: "POST" }).then(() => {}),
resumeSession: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}/resume`, { method: "POST" }).then(() => {}),
stopSession: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}/stop`, { method: "POST" }).then(j<CaptureSession>),
renameSession: (sid: number, title: string) =>
  fetch(`${BASE}/api/capture/sessions/${sid}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) }).then(j<CaptureSession>),
deleteSession: (sid: number) => fetch(`${BASE}/api/capture/sessions/${sid}`, { method: "DELETE" }).then(() => {}),
organizeSession: (sid: number, projectId: number) =>
  fetch(`${BASE}/api/capture/sessions/${sid}/organize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project_id: projectId }) }).then(j<IndexStatus>),
```

- [ ] **Step 2: 实现 — `native.ts` 封装 pywebview(开发态降级)**

```typescript
// frontend/src/lib/native.ts —— 录制相关原生能力封装;无 pywebview(开发态)时静默降级。
type CaptureApi = {
  capture_screenshot?: () => Promise<string | null>;
  start_capture_monitors?: (sid: number, dir: string, sources: string[]) => Promise<unknown>;
  stop_capture_monitors?: () => Promise<void>;
  show_recording_hud?: (sid: number) => Promise<unknown>;
  hide_recording_hud?: () => Promise<void>;
};

function api(): CaptureApi | null {
  return (window as unknown as { pywebview?: { api: CaptureApi } }).pywebview?.api ?? null;
}

export const native = {
  available: () => api() !== null,
  // 截图无参:shell 用 start_capture_monitors 存下的 staging_dir 存图并自行 POST 截图事件。
  screenshot: () => api()?.capture_screenshot?.() ?? Promise.resolve(null),
  startMonitors: (sid: number, dir: string, sources: string[]) => api()?.start_capture_monitors?.(sid, dir, sources) ?? Promise.resolve(null),
  stopMonitors: () => api()?.stop_capture_monitors?.() ?? Promise.resolve(),
  showHud: (sid: number) => api()?.show_recording_hud?.(sid) ?? Promise.resolve(null),
  hideHud: () => api()?.hide_recording_hud?.() ?? Promise.resolve(),
};
```

- [ ] **Step 3: build + 提交**

```bash
cd frontend && npm run build && cd ..
git add frontend/src/lib/api.ts frontend/src/lib/native.ts
git commit -m "feat(capture): frontend capture API client + native wrapper"
```

### Task 14: CaptureView — 源开关 + 开始/进行中 session

**Files:**
- Modify: `frontend/src/views/CaptureView.tsx`(替换占位)

- [ ] **Step 1: 实现**

替换占位内容。状态机:
- **无活动 session**:源开关(笔记/剪贴板/截图 可勾;🎤 外录 / 🔊 内录 = disabled + 「即将到来」)+「开始 session」按钮。
- **有活动 session**:计时(从 `elapsed_seconds` 起算、每秒自增)+ live feed(轮询 `getSession` 每 1–2s,或用 SSE)+ 笔记输入框 + 截图按钮 + 暂停/继续 + 停止。

接线:
- **开始**:`const sess = await api.startSession(sources)` → `native.startMonitors(sess.id, sess.staging_dir, sources)` + `native.showHud(sess.id)`。
- **笔记**:`api.appendEvent(sess.id, "note", 文本)`(前端直接 POST)。
- **截图**:`native.screenshot()`(**无参**;shell 用 `startMonitors` 存下的 `staging_dir` 存图并自行 POST 截图事件 → live feed 随后显示)。`native.available()===false`(开发态)→ 截图按钮禁用 + 提示「需在桌面 app 内」。
- **暂停/继续**:`api.pauseSession(sess.id)` / `api.resumeSession(sess.id)`(同时 `native.stopMonitors()` / 再 `native.startMonitors(...)` 以真正停/起原生采集)。
- **停止**:`native.stopMonitors()` + `native.hideHud()` + `api.stopSession(sess.id)` → 转暂存区。

`staging_dir` 来自 `CaptureSessionOut.staging_dir`(Task 8 已含);前端不展示、只转发给 `native.startMonitors`。HUD 与本视图都能触发笔记/截图/暂停/停止——HUD 是失焦时的入口,本视图是在采集页时的入口,二者调用同一组 `api`/`native`。

- [ ] **Step 2: build + 提交**

```bash
cd frontend && npm run build && cd ..
git add frontend/src/views/CaptureView.tsx
git commit -m "feat(capture): CaptureView — source toggles + active session controls"
```

### Task 15: RecordingHud 组件 + `?view=hud` 分流

**Files:**
- Create: `frontend/src/components/RecordingHud.tsx`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: 实现 HUD 组件**

按图示横排:🔴 + 计时(`elapsed_seconds`,前端每秒自增并定期与后端校准)· ✏️ 笔记(点开内联输入条 → `appendEvent`)· ⌧ 截图(`native.screenshot()`)· 🎤 外录 / 🔊 内录(**disabled** + tooltip「即将到来」)· ⏸ 暂停/继续(`pause`/`resume`)· ⏹ 停止(`stopSession` + `native.hideHud`)· › 收起/展开(本地 state 切换药丸/全条)。session id 从 `?session=` 读。停止后窗口由 shell `hide_recording_hud` 销毁。

- [ ] **Step 2: `main.tsx` 分流**

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { RecordingHud } from '@/components/RecordingHud'

const params = new URLSearchParams(window.location.search)
const isHud = params.get('view') === 'hud'
const sessionId = Number(params.get('session') ?? 0)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {isHud ? <RecordingHud sessionId={sessionId} /> : <App />}
  </StrictMode>,
)
```

- [ ] **Step 3: build + 提交**

```bash
cd frontend && npm run build && cd ..
git add frontend/src/components/RecordingHud.tsx frontend/src/main.tsx
git commit -m "feat(capture): recording HUD component + ?view=hud routing"
```

### Task 16: 采集后暂存区 + 时间线 + 指派到 Project

**Files:**
- Create: `frontend/src/views/CaptureStagingView.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 实现 Staging 视图**

`listSessions()` 列出 raw sessions(按时间倒序,标 status 徽标 recording/staged/organized)。选中一个 staged session → `getSession` 取事件 →**图形时间线 v1**:事件按 `ts` 横向/纵向排列 + 相对时间刻度,每条显示 kind 图标 + 摘要(note/clipboard 文本截断;screenshot 缩略图)。底部「指派到 Project」:下拉选已有 Project(`listProjects`)→ `organizeSession(sid, projectId)` → 成功后调用 App 传入的 `onOrganized(projectId)` 跳「信息处理和入库」看索引进度(复用 Plan 7 的跨页导航)。`recording` 中的 session 显示「录制中」不可指派。

- [ ] **Step 2: `App.tsx` 接采集 tab**

`capture` tab 改为同时承载「开始/进行中」(CaptureView)与「暂存区」(CaptureStagingView)——用一个内部小切换(如顶部「采集 / 暂存」段控),或 CaptureView 内含暂存入口。`onOrganized` 复用现有 `setProcessFocus` + 切到 `process` tab 的逻辑(与 `onReindexStarted` 同形)。最小改动:把 `{activeTab === "capture" && <CaptureView />}` 换成一个 `<CaptureTab onOrganized={(pid)=>{ setProcessFocus(...); setActiveTab("process"); }} />` 包裹两视图。

- [ ] **Step 3: build + 提交**

```bash
cd frontend && npm run build && cd ..
git add frontend/src/views/CaptureStagingView.tsx frontend/src/App.tsx
git commit -m "feat(capture): staging area + timeline + assign-to-Project closing the loop"
```

---

## Phase 6 — 收尾

### Task 17: 全套件 + 全量 build + 文档对齐

- [ ] **Step 1: 后端全套件**

Run: `cd backend && ./.venv/bin/pytest -q`
Expected: 全绿(新增 capture 用例 + 旧用例不破)。

- [ ] **Step 2: 前端 build**

Run: `cd frontend && npm run build`
Expected: exit 0。

- [ ] **Step 3: 更新 CaptureView 顶部「开发中」徽标**

CaptureView 里把残留的「开发中 · Plan 4」徽标去掉(本期采集已可用)。build 后提交。

- [ ] **Step 4: 提交收尾**

```bash
git add -A
git commit -m "chore(capture): finalize Plan 8 capture skeleton"
```

---

## 自审(写完计划后对照 spec)

- **spec §2-3(进程模型/范围)**:Task 9(后端权威)+ Task 10-12(shell 原生)+ Task 13 native 封装覆盖;ASR 仅接口缝 = Task 4 ✓。
- **spec §4 数据模型**:Task 1(两表 + source_session_id)✓。
- **spec §5 接口缝**:`AudioSource`/`Transcriber` = Task 4;`Organizer` = Task 6 ✓。
- **spec §6 三源**:笔记/剪贴板/截图 = Task 11(剪贴板/全局热键)+ Task 10(截图)+ Task 14/15(笔记/截图 UI);声音 disabled = Task 14/15 ✓。
- **spec §7 HUD**:Task 12(窗口)+ Task 15(组件 + 分流)✓。
- **spec §8 数据流 / §9 归类闭环**:Task 7(OrganizeService)+ Task 9(organize→索引)+ Task 16(指派 UI + 跳转)✓。
- **spec §10 API**:Task 8(schemas)+ Task 9(全部端点 + SSE)✓。
- **spec §11 权限降级**:截图/热键失败降级(Task 10/11)、native 开发态降级(Task 13)、HUD 失败降级(Task 12)✓。
- **spec §12 错误边界**:409 单一活动/已 organized、404、空 session(Task 3/7/9 测试覆盖)✓。
- **spec §13 测试**:后端 TDD 全 fake(Task 1-9);shell/前端手测 + build(Phase 4-5)✓。
- **签名一致性**:`capture_screenshot()` 全程无参(Task 10 定义、Task 13 封装、Task 14/15 调用一致),用 `self._cap["dir"]`(Task 11 的 `start_capture_monitors` 所设)存图 + `self._post_event`(Task 10)自行上报;`staging_dir` 经 `CaptureSessionOut`(Task 8)→ `native.startMonitors`(Task 14)传给 shell。已就地统一,无遗留矛盾。
- **占位扫描**:无 TBD;shell/前端任务给了具体代码与降级路径,无「自行处理边界」式空话。
