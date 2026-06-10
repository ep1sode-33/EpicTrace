# Foundation + File-Direct-Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 立起 EpicTrace 的可运行骨架(pywebview 外壳 + FastAPI 后端 + React/shadcn 前端),并打通最简的"文件直接入库"竖切:创建/选择一个本地 Project 文件夹 → 拖入/提交文件(可带描述)→ 文件被复制进 Project 文件夹、入库记录(hash/大小/mtime/方式/时间/描述/纯文本提取)落进本地 SQLite → 前端列出该 Project 的已入库文件。

**Architecture:** 后端是本地 FastAPI 服务,SQLAlchemy 2.0 + SQLite 做关系型元数据库;五个抽象接口(`LLMProvider` / `EmbeddingProvider` / `VectorStore` / `Segmenter` / `MediaProcessor`)以 ABC 形式从第一天就立好"接口缝",本计划只实现 `MediaProcessor`(文本)与 `IdentitySegmenter`,其余先放 ABC。前端 React+Vite+shadcn,通过 HTTP 调后端;pywebview 外壳负责启动 uvicorn 并开窗。**不含 embedding/向量库/LLM 调用**(留给后续 Plan 2/3)。

**Tech Stack:** Python 3.11+ · uv · FastAPI · uvicorn · SQLAlchemy 2.0 · pydantic v2 · pytest · httpx · React 18 · Vite · TypeScript · TailwindCSS · shadcn/ui · pywebview

**关键约定:** 文档/代码/提交信息中**不出现任何前身原型的产品代号**。

---

## File Structure

```
EpicTrace/
  backend/
    pyproject.toml
    epictrace/
      __init__.py
      config.py              # AppConfig:数据目录、db 路径、(预留)按角色 LLM 配置
      db.py                  # SQLAlchemy engine/session/Base
      models.py              # Project, IngestRecord ORM
      schemas.py             # pydantic 出入参 DTO
      interfaces/
        __init__.py
        llm.py               # LLMProvider (ABC, 仅签名)
        embedding.py         # EmbeddingProvider (ABC, 仅签名)
        vector_store.py      # VectorStore (ABC, 仅签名)
        segmenter.py         # Segmenter (ABC) + IdentitySegmenter (实现)
        media.py             # MediaProcessor (ABC) + MediaResult + 注册表
      media/
        __init__.py
        text.py              # TextMediaProcessor (.md/.txt)
      services/
        __init__.py
        projects.py          # ProjectService.create / list
        ingest.py            # IngestService.ingest_file
      api/
        __init__.py
        deps.py              # get_db 依赖
        app.py               # create_app():装路由、(prod)挂前端静态
        routers/
          __init__.py
          health.py
          projects.py
          files.py
      main.py                # uvicorn 入口
    tests/
      conftest.py
      test_health.py
      test_media_text.py
      test_projects_service.py
      test_ingest_service.py
      test_api_projects.py
      test_api_files.py
  frontend/
    package.json / vite.config.ts / tsconfig.json / tailwind.config.js / index.html
    src/
      main.tsx / App.tsx / index.css
      lib/api.ts
      lib/utils.ts
      components/ui/button.tsx / input.tsx / card.tsx   # shadcn
      components/ProjectPanel.tsx
      components/IngestPanel.tsx
  shell/
    run.py                   # pywebview 启动 uvicorn + 开窗
  README.md
```

每个文件单一职责:`models.py` 只放 ORM;`services/*` 放业务逻辑(可独立单测,不依赖 FastAPI);`api/routers/*` 只做 HTTP 适配(薄)。`interfaces/` 是换件不返工的缝。

---

## Task 1: 后端工程骨架 + 健康检查端点

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/epictrace/__init__.py`
- Create: `backend/epictrace/api/__init__.py`, `backend/epictrace/api/routers/__init__.py`
- Create: `backend/epictrace/api/routers/health.py`
- Create: `backend/epictrace/api/app.py`
- Create: `backend/epictrace/main.py`
- Test: `backend/tests/test_health.py`, `backend/tests/conftest.py`

- [ ] **Step 1: 初始化 uv 工程与依赖**

Run:
```bash
cd backend
uv init --no-readme --python 3.11
uv add fastapi "uvicorn[standard]" "sqlalchemy>=2.0" "pydantic>=2"
uv add --dev pytest httpx
```
覆盖 `backend/pyproject.toml` 中 `[project]` 的包配置,确保 `epictrace` 包可被发现(uv init 生成的 `name` 可能不同,改成 `epictrace`)。

- [ ] **Step 2: 写失败测试 `tests/test_health.py`**

```python
from fastapi.testclient import TestClient
from epictrace.api.app import create_app


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

`tests/conftest.py`(空占位即可,后续任务填充):
```python
# shared pytest fixtures live here
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: epictrace.api.app`

- [ ] **Step 4: 实现最小代码**

`backend/epictrace/__init__.py`:
```python
__all__ = []
```

`backend/epictrace/api/routers/health.py`:
```python
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

`backend/epictrace/api/app.py`:
```python
from fastapi import FastAPI

from epictrace.api.routers import health


def create_app() -> FastAPI:
    app = FastAPI(title="EpicTrace")
    app.include_router(health.router)
    return app
```

`backend/epictrace/api/__init__.py` 和 `backend/epictrace/api/routers/__init__.py`:留空。

`backend/epictrace/main.py`:
```python
import uvicorn

from epictrace.api.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add backend/
git commit -m "feat(backend): FastAPI 骨架 + 健康检查端点"
```

---

## Task 2: 配置模块(数据目录 / db 路径 / 预留按角色 LLM 配置)

**Files:**
- Create: `backend/epictrace/config.py`
- Test: `backend/tests/test_config.py`

- [ ] **Step 1: 写失败测试 `tests/test_config.py`**

```python
from pathlib import Path

from epictrace.config import AppConfig


def test_config_uses_given_data_dir(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    assert cfg.data_dir == tmp_path
    assert cfg.db_path == tmp_path / "epictrace.db"
    assert cfg.sqlalchemy_url == f"sqlite:///{tmp_path / 'epictrace.db'}"


def test_config_default_data_dir_is_created():
    cfg = AppConfig()
    assert cfg.data_dir.exists()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'AppConfig'`

- [ ] **Step 3: 实现 `backend/epictrace/config.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    d = Path.home() / ".epictrace"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass(frozen=True)
class LLMRoleConfig:
    """按角色的 LLM 配置(本计划未使用,先立结构;OpenAI-compatible)。"""
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-chat"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path = field(default_factory=_default_data_dir)
    # 预留:agent / chat / caption 各自端点+key+模型(后续 Plan 用)
    agent_llm: LLMRoleConfig = field(default_factory=LLMRoleConfig)
    chat_llm: LLMRoleConfig = field(default_factory=LLMRoleConfig)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "epictrace.db"

    @property
    def sqlalchemy_url(self) -> str:
        return f"sqlite:///{self.db_path}"
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/config.py backend/tests/test_config.py
git commit -m "feat(backend): AppConfig(数据目录/db 路径/预留按角色 LLM 配置)"
```

---

## Task 3: 数据库层(SQLAlchemy engine / session / Base)

**Files:**
- Create: `backend/epictrace/db.py`
- Test: `backend/tests/test_db.py`

- [ ] **Step 1: 写失败测试 `tests/test_db.py`**

```python
from pathlib import Path

from sqlalchemy import text

from epictrace.config import AppConfig
from epictrace.db import Database


def test_database_session_executes(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    with db.session() as s:
        assert s.execute(text("select 1")).scalar_one() == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_db.py -v`
Expected: FAIL — `ImportError: cannot import name 'Database'`

- [ ] **Step 3: 实现 `backend/epictrace/db.py`**

```python
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from epictrace.config import AppConfig


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, config: AppConfig) -> None:
        self._engine = create_engine(
            config.sqlalchemy_url,
            connect_args={"check_same_thread": False},
        )
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)

    def create_all(self) -> None:
        # 确保所有 model 已 import 后再建表
        from epictrace import models  # noqa: F401

        Base.metadata.create_all(self._engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/db.py backend/tests/test_db.py
git commit -m "feat(backend): SQLAlchemy Database(engine/session/Base)"
```

---

## Task 4: ORM 模型(Project / IngestRecord)

**Files:**
- Create: `backend/epictrace/models.py`
- Test: `backend/tests/test_models.py`

- [ ] **Step 1: 写失败测试 `tests/test_models.py`**

```python
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.models import IngestRecord, Project


def test_project_and_ingest_record_persist(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    with db.session() as s:
        proj = Project(title="CS 2506", folder_path=str(tmp_path / "CS 2506"))
        s.add(proj)
        s.flush()
        rec = IngestRecord(
            project_id=proj.id,
            original_filename="lecture.md",
            stored_path=str(tmp_path / "CS 2506" / "lecture.md"),
            content_hash="abc123",
            size_bytes=10,
            mtime=1.5,
            ingest_method="file_direct",
            description="virtual memory",
            extracted_text="hello",
        )
        s.add(rec)
        s.flush()
        assert proj.id is not None
        assert rec.id is not None
        assert rec.project_id == proj.id
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'Project'`

- [ ] **Step 3: 实现 `backend/epictrace/models.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from epictrace.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    folder_path: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    ingest_records: Mapped[list["IngestRecord"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class IngestRecord(Base):
    __tablename__ = "ingest_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]
    mtime: Mapped[float]
    ingest_method: Mapped[str] = mapped_column(String(32))  # file_direct / drag / session
    description: Mapped[str] = mapped_column(Text, default="")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="ingest_records")
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/models.py backend/tests/test_models.py
git commit -m "feat(backend): Project / IngestRecord ORM 模型"
```

---

## Task 5: 五个抽象接口("接口缝")

**Files:**
- Create: `backend/epictrace/interfaces/__init__.py`
- Create: `backend/epictrace/interfaces/llm.py`, `embedding.py`, `vector_store.py`, `segmenter.py`, `media.py`
- Test: `backend/tests/test_interfaces.py`

- [ ] **Step 1: 写失败测试 `tests/test_interfaces.py`**

```python
from epictrace.interfaces.segmenter import IdentitySegmenter, Segment
from epictrace.interfaces.media import MediaProcessor
from epictrace.interfaces.llm import LLMProvider
from epictrace.interfaces.embedding import EmbeddingProvider
from epictrace.interfaces.vector_store import VectorStore


def test_identity_segmenter_returns_single_segment():
    seg = IdentitySegmenter()
    events = [{"t": 0}, {"t": 1}, {"t": 2}]
    result = seg.segment(events, hint=None)
    assert len(result) == 1
    assert result[0].event_indices == [0, 1, 2]


def test_abcs_cannot_be_instantiated():
    for abc in (MediaProcessor, LLMProvider, EmbeddingProvider, VectorStore):
        try:
            abc()  # type: ignore[abstract]
            assert False, f"{abc.__name__} 不应可实例化"
        except TypeError:
            pass
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_interfaces.py -v`
Expected: FAIL — `ModuleNotFoundError: epictrace.interfaces.segmenter`

- [ ] **Step 3: 实现接口**

`backend/epictrace/interfaces/__init__.py`:留空。

`backend/epictrace/interfaces/segmenter.py`:
```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    event_indices: list[int]
    project_hint: str | None = None


class Segmenter(ABC):
    @abstractmethod
    def segment(self, events: list[dict], hint: str | None) -> list[Segment]: ...


class IdentitySegmenter(Segmenter):
    """默认:整段 = 1 段。以后换 LLM 切割时只替换本类。"""

    def segment(self, events: list[dict], hint: str | None) -> list[Segment]:
        return [Segment(event_indices=list(range(len(events))), project_hint=hint)]
```

`backend/epictrace/interfaces/media.py`:
```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MediaResult:
    text: str
    metadata: dict = field(default_factory=dict)


class MediaProcessor(ABC):
    @abstractmethod
    def supports(self, path: Path) -> bool: ...

    @abstractmethod
    def process(self, path: Path) -> MediaResult: ...
```

`backend/epictrace/interfaces/llm.py`:
```python
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """OpenAI-compatible 抽象。实现留给后续 Plan(DeepSeek 等)。"""

    @abstractmethod
    def complete(self, messages: list[dict], **kwargs) -> str: ...
```

`backend/epictrace/interfaces/embedding.py`:
```python
from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """实现留给后续 Plan(BGE-M3 本地等)。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def model_id(self) -> str: ...
```

`backend/epictrace/interfaces/vector_store.py`:
```python
from __future__ import annotations

from abc import ABC, abstractmethod


class VectorStore(ABC):
    """实现留给后续 Plan(MilvusLiteStore)。"""

    @abstractmethod
    def upsert(self, records: list[dict]) -> None: ...

    @abstractmethod
    def query(self, vector: list[float], filter: dict | None, k: int) -> list[dict]: ...
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_interfaces.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/interfaces/ backend/tests/test_interfaces.py
git commit -m "feat(backend): 五个抽象接口(LLM/Embedding/VectorStore/Segmenter/Media)+ IdentitySegmenter"
```

---

## Task 6: 文本 MediaProcessor(.md / .txt)+ 注册表

**Files:**
- Create: `backend/epictrace/media/__init__.py`
- Create: `backend/epictrace/media/text.py`
- Test: `backend/tests/test_media_text.py`

- [ ] **Step 1: 写失败测试 `tests/test_media_text.py`**

```python
from pathlib import Path

from epictrace.media import get_processor
from epictrace.media.text import TextMediaProcessor


def test_text_processor_reads_markdown(tmp_path: Path):
    f = tmp_path / "note.md"
    f.write_text("# Title\nhello world", encoding="utf-8")
    proc = TextMediaProcessor()
    assert proc.supports(f) is True
    result = proc.process(f)
    assert "hello world" in result.text
    assert result.metadata["chars"] == len("# Title\nhello world")


def test_registry_returns_text_processor_for_txt(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    proc = get_processor(f)
    assert isinstance(proc, TextMediaProcessor)


def test_registry_returns_none_for_unknown(tmp_path: Path):
    assert get_processor(tmp_path / "a.pdf") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_media_text.py -v`
Expected: FAIL — `ModuleNotFoundError: epictrace.media`

- [ ] **Step 3: 实现**

`backend/epictrace/media/text.py`:
```python
from __future__ import annotations

from pathlib import Path

from epictrace.interfaces.media import MediaProcessor, MediaResult

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text"}


class TextMediaProcessor(MediaProcessor):
    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in TEXT_SUFFIXES

    def process(self, path: Path) -> MediaResult:
        text = path.read_text(encoding="utf-8", errors="replace")
        return MediaResult(text=text, metadata={"chars": len(text)})
```

`backend/epictrace/media/__init__.py`:
```python
from __future__ import annotations

from pathlib import Path

from epictrace.interfaces.media import MediaProcessor
from epictrace.media.text import TextMediaProcessor

# 注册表:以后加 pdf/docx/ppt/image processor 时只往这里追加(Plan 6)
_PROCESSORS: list[MediaProcessor] = [TextMediaProcessor()]


def get_processor(path: Path) -> MediaProcessor | None:
    for proc in _PROCESSORS:
        if proc.supports(path):
            return proc
    return None
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_media_text.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/media/ backend/tests/test_media_text.py
git commit -m "feat(backend): TextMediaProcessor(.md/.txt)+ processor 注册表"
```

---

## Task 7: ProjectService(create / list)

**Files:**
- Create: `backend/epictrace/services/__init__.py`
- Create: `backend/epictrace/services/projects.py`
- Test: `backend/tests/test_projects_service.py`

- [ ] **Step 1: 写失败测试 `tests/test_projects_service.py`**

```python
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.services.projects import ProjectService


def _db(tmp_path: Path) -> Database:
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    return db


def test_create_project_creates_folder_and_row(tmp_path: Path):
    db = _db(tmp_path)
    folder = tmp_path / "CS 2506"
    svc = ProjectService(db)
    proj = svc.create(title="CS 2506", folder_path=str(folder))
    assert proj.id is not None
    assert folder.exists()  # 文件夹被创建
    assert [p.title for p in svc.list()] == ["CS 2506"]


def test_list_empty(tmp_path: Path):
    assert ProjectService(_db(tmp_path)).list() == []
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_projects_service.py -v`
Expected: FAIL — `ModuleNotFoundError: epictrace.services.projects`

- [ ] **Step 3: 实现**

`backend/epictrace/services/__init__.py`:留空。

`backend/epictrace/services/projects.py`:
```python
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.models import Project


class ProjectService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, title: str, folder_path: str) -> Project:
        Path(folder_path).mkdir(parents=True, exist_ok=True)
        with self._db.session() as s:
            proj = Project(title=title, folder_path=folder_path)
            s.add(proj)
            s.flush()
            s.refresh(proj)
            s.expunge(proj)
            return proj

    def list(self) -> list[Project]:
        with self._db.session() as s:
            rows = s.execute(select(Project).order_by(Project.created_at)).scalars().all()
            for r in rows:
                s.expunge(r)
            return list(rows)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_projects_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/services/__init__.py backend/epictrace/services/projects.py backend/tests/test_projects_service.py
git commit -m "feat(backend): ProjectService(create 建文件夹+落库 / list)"
```

---

## Task 8: IngestService(复制文件 + 计算 hash/大小/mtime + 提取文本 + 落库)

**Files:**
- Create: `backend/epictrace/services/ingest.py`
- Test: `backend/tests/test_ingest_service.py`

- [ ] **Step 1: 写失败测试 `tests/test_ingest_service.py`**

```python
import hashlib
from pathlib import Path

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.services.ingest import IngestService
from epictrace.services.projects import ProjectService


def _setup(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    return db, proj


def test_ingest_copies_file_and_records_metadata(tmp_path: Path):
    db, proj = _setup(tmp_path)
    src = tmp_path / "src" / "note.md"
    src.parent.mkdir()
    src.write_text("# vm\nvirtual memory", encoding="utf-8")

    svc = IngestService(db)
    rec = svc.ingest_file(
        project_id=proj.id,
        source_path=str(src),
        ingest_method="file_direct",
        description="5/13 CS2506 PPT",
    )

    stored = Path(rec.stored_path)
    assert stored.exists()
    assert stored.parent == Path(proj.folder_path)        # 复制进 Project 文件夹
    assert rec.content_hash == hashlib.sha256(src.read_bytes()).hexdigest()
    assert rec.size_bytes == src.stat().st_size
    assert rec.ingest_method == "file_direct"
    assert rec.description == "5/13 CS2506 PPT"
    assert "virtual memory" in rec.extracted_text   # 文本被提取


def test_ingest_unknown_type_leaves_text_empty(tmp_path: Path):
    db, proj = _setup(tmp_path)
    src = tmp_path / "a.bin"
    src.write_bytes(b"\x00\x01")
    rec = IngestService(db).ingest_file(
        project_id=proj.id, source_path=str(src), ingest_method="file_direct", description=""
    )
    assert rec.extracted_text == ""
    assert Path(rec.stored_path).exists()


def test_ingest_avoids_overwriting_same_name(tmp_path: Path):
    db, proj = _setup(tmp_path)
    src = tmp_path / "dup.txt"
    src.write_text("a", encoding="utf-8")
    svc = IngestService(db)
    r1 = svc.ingest_file(project_id=proj.id, source_path=str(src), ingest_method="file_direct", description="")
    r2 = svc.ingest_file(project_id=proj.id, source_path=str(src), ingest_method="file_direct", description="")
    assert r1.stored_path != r2.stored_path   # 重名不覆盖
    assert Path(r1.stored_path).exists() and Path(r2.stored_path).exists()


def test_list_for_project(tmp_path: Path):
    db, proj = _setup(tmp_path)
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    svc = IngestService(db)
    svc.ingest_file(project_id=proj.id, source_path=str(src), ingest_method="file_direct", description="")
    assert len(svc.list_for_project(proj.id)) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_ingest_service.py -v`
Expected: FAIL — `ModuleNotFoundError: epictrace.services.ingest`

- [ ] **Step 3: 实现 `backend/epictrace/services/ingest.py`**

```python
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.media import get_processor
from epictrace.models import IngestRecord, Project


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _unique_dest(folder: Path, filename: str) -> Path:
    dest = folder / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    i = 1
    while True:
        candidate = folder / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


class IngestService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def ingest_file(
        self, project_id: int, source_path: str, ingest_method: str, description: str
    ) -> IngestRecord:
        src = Path(source_path)
        with self._db.session() as s:
            project = s.get(Project, project_id)
            if project is None:
                raise ValueError(f"project {project_id} not found")
            folder = Path(project.folder_path)
            folder.mkdir(parents=True, exist_ok=True)

            dest = _unique_dest(folder, src.name)
            shutil.copy2(src, dest)

            proc = get_processor(dest)
            extracted = proc.process(dest).text if proc is not None else ""

            rec = IngestRecord(
                project_id=project_id,
                original_filename=src.name,
                stored_path=str(dest),
                content_hash=_sha256(dest),
                size_bytes=dest.stat().st_size,
                mtime=dest.stat().st_mtime,
                ingest_method=ingest_method,
                description=description,
                extracted_text=extracted,
            )
            s.add(rec)
            s.flush()
            s.refresh(rec)
            s.expunge(rec)
            return rec

    def list_for_project(self, project_id: int) -> list[IngestRecord]:
        with self._db.session() as s:
            rows = (
                s.execute(
                    select(IngestRecord)
                    .where(IngestRecord.project_id == project_id)
                    .order_by(IngestRecord.created_at)
                )
                .scalars()
                .all()
            )
            for r in rows:
                s.expunge(r)
            return list(rows)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_ingest_service.py -v`
Expected: PASS(4 个测试)

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/services/ingest.py backend/tests/test_ingest_service.py
git commit -m "feat(backend): IngestService(复制+hash/大小/mtime+文本提取+落库,重名不覆盖)"
```

---

## Task 9: FastAPI 路由(projects + files)与 DTO

**Files:**
- Create: `backend/epictrace/schemas.py`
- Create: `backend/epictrace/api/deps.py`
- Create: `backend/epictrace/api/routers/projects.py`, `backend/epictrace/api/routers/files.py`
- Modify: `backend/epictrace/api/app.py`(装新路由 + 建表 + CORS)
- Test: `backend/tests/test_api_projects.py`, `backend/tests/test_api_files.py`

- [ ] **Step 1: 写失败测试 `tests/test_api_projects.py` 和 `tests/test_api_files.py`**

`tests/conftest.py`(替换占位内容):
```python
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from epictrace.api.app import create_app
from epictrace.config import AppConfig
from epictrace.db import Database


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    app = create_app(db=db)
    return TestClient(app)
```

`tests/test_api_projects.py`:
```python
def test_create_and_list_projects(client, tmp_path):
    folder = str(tmp_path / "CS 2506")
    resp = client.post("/api/projects", json={"title": "CS 2506", "folder_path": folder})
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "CS 2506"
    assert body["id"] > 0

    listed = client.get("/api/projects").json()
    assert len(listed) == 1
    assert listed[0]["folder_path"] == folder
```

`tests/test_api_files.py`:
```python
from pathlib import Path


def test_ingest_file_and_list(client, tmp_path):
    folder = str(tmp_path / "P")
    pid = client.post("/api/projects", json={"title": "P", "folder_path": folder}).json()["id"]

    src = tmp_path / "note.md"
    src.write_text("# vm\nvirtual memory", encoding="utf-8")

    resp = client.post(
        "/api/files/ingest",
        json={
            "project_id": pid,
            "source_path": str(src),
            "ingest_method": "file_direct",
            "description": "lecture",
        },
    )
    assert resp.status_code == 201
    rec = resp.json()
    assert rec["original_filename"] == "note.md"
    assert Path(rec["stored_path"]).exists()

    listed = client.get(f"/api/files?project_id={pid}").json()
    assert len(listed) == 1
    assert listed[0]["description"] == "lecture"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_api_projects.py tests/test_api_files.py -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'db'` / 404

- [ ] **Step 3: 实现 DTO、依赖、路由,并改 app 工厂**

`backend/epictrace/schemas.py`:
```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    title: str
    folder_path: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    folder_path: str
    created_at: datetime


class IngestRequest(BaseModel):
    project_id: int
    source_path: str
    ingest_method: str = "file_direct"
    description: str = ""


class IngestRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    original_filename: str
    stored_path: str
    content_hash: str
    size_bytes: int
    ingest_method: str
    description: str
    created_at: datetime
```

`backend/epictrace/api/deps.py`:
```python
from __future__ import annotations

from fastapi import Request

from epictrace.db import Database


def get_db(request: Request) -> Database:
    return request.app.state.db
```

`backend/epictrace/api/routers/projects.py`:
```python
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from epictrace.api.deps import get_db
from epictrace.db import Database
from epictrace.schemas import ProjectCreate, ProjectOut
from epictrace.services.projects import ProjectService

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Database = Depends(get_db)) -> ProjectOut:
    proj = ProjectService(db).create(title=payload.title, folder_path=payload.folder_path)
    return ProjectOut.model_validate(proj)


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Database = Depends(get_db)) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in ProjectService(db).list()]
```

`backend/epictrace/api/routers/files.py`:
```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from epictrace.api.deps import get_db
from epictrace.db import Database
from epictrace.schemas import IngestRecordOut, IngestRequest
from epictrace.services.ingest import IngestService

router = APIRouter(prefix="/api/files", tags=["files"])


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
```

替换 `backend/epictrace/api/app.py`:
```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.api.routers import files, health, projects


def create_app(db: Database | None = None) -> FastAPI:
    app = FastAPI(title="EpicTrace")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],  # Vite dev server
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if db is None:
        db = Database(AppConfig())
        db.create_all()
    app.state.db = db

    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(files.router)
    return app
```

- [ ] **Step 4: 运行全部后端测试确认通过**

Run: `cd backend && uv run pytest -v`
Expected: PASS(全部)

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/schemas.py backend/epictrace/api/ backend/tests/conftest.py backend/tests/test_api_projects.py backend/tests/test_api_files.py
git commit -m "feat(backend): projects/files 路由 + DTO + app 工厂(注入 db、CORS)"
```

---

## Task 10: 前端脚手架(Vite + React + TS + Tailwind + shadcn)

**Files:**
- Create: 整个 `frontend/`(脚手架)
- Create: `frontend/src/lib/api.ts`

- [ ] **Step 1: 脚手架与依赖**

Run:
```bash
cd EpicTrace
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p
npm install class-variance-authority clsx tailwind-merge lucide-react
```
按 shadcn 文档配置 `tailwind.config.js`(content 指向 `./index.html` 与 `./src/**/*.{ts,tsx}`)、在 `src/index.css` 顶部加 `@tailwind base; @tailwind components; @tailwind utilities;`,并初始化 shadcn:
```bash
npx shadcn@latest init   # 选 default 风格、CSS 变量
npx shadcn@latest add button input card textarea
```

- [ ] **Step 2: 写 API 客户端 `frontend/src/lib/api.ts`**

```ts
const BASE = "http://localhost:8765";

export interface Project { id: number; title: string; folder_path: string; created_at: string; }
export interface IngestRecord {
  id: number; project_id: number; original_filename: string; stored_path: string;
  content_hash: string; size_bytes: number; ingest_method: string; description: string; created_at: string;
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  listProjects: () => fetch(`${BASE}/api/projects`).then(j<Project[]>),
  createProject: (title: string, folder_path: string) =>
    fetch(`${BASE}/api/projects`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, folder_path }),
    }).then(j<Project>),
  listFiles: (projectId: number) =>
    fetch(`${BASE}/api/files?project_id=${projectId}`).then(j<IngestRecord[]>),
  ingestFile: (project_id: number, source_path: string, description: string) =>
    fetch(`${BASE}/api/files/ingest`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id, source_path, ingest_method: "file_direct", description }),
    }).then(j<IngestRecord>),
};
```

- [ ] **Step 3: 冒烟验证前端能起**

Run: `cd frontend && npm run dev`
Expected: Vite 在 `http://localhost:5173` 启动,默认页面可打开(此时还没接业务 UI)。

- [ ] **Step 4: 提交**

```bash
git add frontend/
git commit -m "feat(frontend): Vite+React+TS+Tailwind+shadcn 脚手架 + API 客户端"
```

> 注:`frontend/node_modules/` 已被根 `.gitignore` 排除。

---

## Task 11: 前端业务 UI(Project 面板 + 入库面板 + 文件列表)

**Files:**
- Create: `frontend/src/components/ProjectPanel.tsx`, `frontend/src/components/IngestPanel.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: `frontend/src/components/ProjectPanel.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api, type Project } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function ProjectPanel({
  selected, onSelect,
}: { selected: Project | null; onSelect: (p: Project) => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [title, setTitle] = useState("");
  const [folder, setFolder] = useState("");

  const refresh = () => api.listProjects().then(setProjects).catch(console.error);
  useEffect(() => { refresh(); }, []);

  const create = async () => {
    if (!title || !folder) return;
    const p = await api.createProject(title, folder);
    setTitle(""); setFolder("");
    await refresh();
    onSelect(p);
  };

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">Projects</h2>
      <div className="flex gap-2">
        <Input placeholder="title (e.g. CS 2506)" value={title} onChange={(e) => setTitle(e.target.value)} />
        <Input placeholder="folder path (/Users/.../CS 2506)" value={folder} onChange={(e) => setFolder(e.target.value)} />
        <Button onClick={create}>Create</Button>
      </div>
      <ul className="space-y-1">
        {projects.map((p) => (
          <li key={p.id}>
            <button
              className={`w-full text-left px-2 py-1 rounded ${selected?.id === p.id ? "bg-accent" : "hover:bg-muted"}`}
              onClick={() => onSelect(p)}
            >
              {p.title} <span className="text-xs text-muted-foreground">{p.folder_path}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 2: `frontend/src/components/IngestPanel.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api, type IngestRecord, type Project } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export function IngestPanel({ project }: { project: Project }) {
  const [files, setFiles] = useState<IngestRecord[]>([]);
  const [path, setPath] = useState("");
  const [desc, setDesc] = useState("");

  const refresh = () => api.listFiles(project.id).then(setFiles).catch(console.error);
  useEffect(() => { refresh(); }, [project.id]);

  const ingest = async () => {
    if (!path) return;
    await api.ingestFile(project.id, path, desc);
    setPath(""); setDesc("");
    await refresh();
  };

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">Ingest into “{project.title}”</h2>
      <Input placeholder="absolute file path" value={path} onChange={(e) => setPath(e.target.value)} />
      <Textarea placeholder="optional description (按 Enter 留空也行)" value={desc} onChange={(e) => setDesc(e.target.value)} />
      <Button onClick={ingest}>Ingest file</Button>
      <ul className="text-sm space-y-1">
        {files.map((f) => (
          <li key={f.id} className="border-b py-1">
            <b>{f.original_filename}</b> · {f.size_bytes}B · {f.description}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 3: 替换 `frontend/src/App.tsx`**

```tsx
import { useState } from "react";
import { ProjectPanel } from "@/components/ProjectPanel";
import { IngestPanel } from "@/components/IngestPanel";
import { type Project } from "@/lib/api";

export default function App() {
  const [selected, setSelected] = useState<Project | null>(null);
  return (
    <div className="grid grid-cols-2 gap-8 p-8 max-w-5xl mx-auto">
      <ProjectPanel selected={selected} onSelect={setSelected} />
      {selected ? <IngestPanel project={selected} /> : <p className="text-muted-foreground">← 选或建一个 Project</p>}
    </div>
  );
}
```

- [ ] **Step 4: 端到端手测(前后端分别起)**

Run(两个终端):
```bash
cd backend && uv run uvicorn epictrace.main:app --port 8765
cd frontend && npm run dev
```
打开 `http://localhost:5173`:建一个 Project(title + 一个真实存在的本地文件夹路径)→ 选中 → 填一个真实文件的绝对路径 + 描述 → Ingest → 文件出现在列表,且该文件被复制进了 Project 文件夹。
Expected: 列表出现该文件;`ls "<folder>"` 能看到被复制进去的文件。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/
git commit -m "feat(frontend): Project 面板 + 文件入库面板 + 文件列表"
```

---

## Task 12: pywebview 外壳(启动 uvicorn + 开窗)+ 生产静态托管 + README

**Files:**
- Create: `shell/run.py`
- Modify: `backend/epictrace/api/app.py`(prod 模式挂前端构建产物)
- Create: `README.md`
- 依赖:`cd backend && uv add pywebview`

- [ ] **Step 1: app 工厂支持挂前端静态(prod)**

在 `backend/epictrace/api/app.py` 的 `create_app` 末尾(`return app` 之前)追加:
```python
    import os
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles

    dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
```
(挂载放在所有 `/api` 路由之后,避免覆盖。)

- [ ] **Step 2: `shell/run.py`**

```python
"""EpicTrace 桌面外壳:后台起 uvicorn,再用 pywebview 开窗。"""
from __future__ import annotations

import threading
import time

import uvicorn
import webview

from epictrace.api.app import create_app

HOST, PORT = "127.0.0.1", 8765


def _serve() -> None:
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")


def main() -> None:
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    time.sleep(1.0)  # 等服务起来
    webview.create_window("EpicTrace", f"http://{HOST}:{PORT}", width=1100, height=750)
    webview.start()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 构建前端并跑外壳验证**

Run:
```bash
cd frontend && npm run build      # 产出 frontend/dist
cd ../backend && uv run python ../shell/run.py
```
Expected: 弹出一个原生窗口,标题 EpicTrace,加载到业务 UI;建 Project、入库文件全程在窗口内可用。

- [ ] **Step 4: 写 `README.md`(开发与运行说明)**

```markdown
# EpicTrace

本地优先的 AI session memory / knowledge workspace。设计见 `docs/superpowers/specs/`,实现计划见 `docs/superpowers/plans/`。

## 开发
后端:`cd backend && uv run pytest`(测试) / `uv run uvicorn epictrace.main:app --port 8765`
前端:`cd frontend && npm run dev`(http://localhost:5173)

## 跑桌面 app
1. `cd frontend && npm run build`
2. `cd backend && uv run python ../shell/run.py`

## 当前能力(Foundation + 文件直接入库)
创建/选择本地 Project 文件夹;提交文件(可带描述)→ 复制进 Project 文件夹 + 入库记录(hash/大小/mtime/方式/时间/文本提取)落 SQLite;列出文件。
尚未包含:embedding/向量库/RAG/对话/采集(见后续 plans)。
```

- [ ] **Step 5: 提交**

```bash
git add shell/run.py backend/epictrace/api/app.py README.md backend/pyproject.toml backend/uv.lock
git commit -m "feat(shell): pywebview 外壳启动 uvicorn+开窗;prod 挂前端静态;README"
```

---

## Verification(端到端)

1. **后端单测全绿:** `cd backend && uv run pytest -v` → 全部 PASS。
2. **手测竖切(桌面 app):** `cd frontend && npm run build && cd ../backend && uv run python ../shell/run.py` → 窗口内:
   - 建 Project(title=`CS 2506`,folder=一个真实本地路径)→ 列表出现、文件夹被创建。
   - 选中 → 填一个真实 `.md` 文件绝对路径 + 描述 → Ingest → 文件出现在列表。
   - 在 Finder/`ls` 里确认该文件**已被复制进 Project 文件夹**。
   - 用 `sqlite3 ~/.epictrace/epictrace.db "select original_filename, content_hash, ingest_method, description from ingest_records;"` 确认记录与 hash 落库。
3. **接口缝就位:** `backend/epictrace/interfaces/` 五个 ABC 存在;`IdentitySegmenter`、`TextMediaProcessor` 有实现且被测试覆盖。

---

## Self-Review 备注(本计划对 spec 的覆盖)

- ✅ Project = 用户控制的本地文件夹(create 时建/指定文件夹)。
- ✅ 文件直接入库 + 入库方式/时间记录(`ingest_method` / `created_at`)+ 描述。
- ✅ 五个接口缝从第一天立好(本计划只实现 Media/Segmenter,其余 ABC)。
- ✅ 事实来源在磁盘(文件复制进 Project 文件夹);元数据落 SQLite。
- ⛔(本计划不含,后续 Plan):整理归类 Agent、embedding/向量库、RAG/对话、采集、多媒体非文本解析、文件 hash 对账 UI、Langfuse。
- 注:本计划的"文件直接入库"是**简化版**——直接选定目标 Project,**跳过整理归类 Agent**(留给后续 Plan 8 增强)。
