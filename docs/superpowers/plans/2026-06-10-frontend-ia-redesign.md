# Frontend IA Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Backend tasks are TDD with pytest. **Frontend tasks use the `frontend-design` skill** to produce polished components, and are gated on `npm run build` succeeding + a described manual check (frontend unit tests are out of scope here). Steps use checkbox (`- [ ]`).

**Goal:** 用顶栏三入口(采集 / 信息处理和入库 / 项目与对话)的 IA 取代旧两面板脚手架;创建项目走原生 Finder 文件夹选择器并接受非空文件夹,既有内容经扫描进入「待索引」;对话/采集/外部入库/建索引按 spec 占位。

**Architecture:** 前端 React + Vite + Tailwind v4 + shadcn,三 tab 外壳;原生文件/文件夹选择经 pywebview `js_api` 桥(开发态回退)。后端补三项:`IngestRecord` 加 `indexed` 状态、文件夹扫描/重扫 service+endpoint(就地登记不复制)、shell 注入 `pick_folder/pick_file`。无文件系统监听,全程用户触发。

**Tech Stack:** Python 3.11 (venv) · FastAPI · SQLAlchemy 2.0 · pytest · React 19 · Vite · Tailwind v4 · shadcn/ui · pywebview · frontend-design skill

**Spec:** `docs/superpowers/specs/2026-06-10-epictrace-frontend-ia-design.md`. **约定:不出现任何前身原型代号。** Git 身份用已配置的 `ep1sode-33`(plain `git commit`,无 `-c`),每条提交带 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>` 尾。分支 `feat/foundation-file-ingest`。venv 用 `.venv/bin/<tool>`。

---

## File Structure

```
backend/epictrace/
  models.py                      # MODIFY: IngestRecord += indexed; ingest_method 增加 "folder_scan"
  schemas.py                     # MODIFY: IngestRecordOut += indexed; new ScanResult DTO
  services/scan.py               # CREATE: 文件夹扫描 + 重扫(就地登记,套忽略规则,diff)
  api/routers/projects.py        # MODIFY: + POST /projects/{id}/scan
  tests/test_scan_service.py     # CREATE
  tests/test_api_scan.py         # CREATE
shell/run.py                     # MODIFY: 注入 js_api(pick_folder/pick_file)
frontend/src/
  lib/pickers.ts                 # CREATE: pickFolder/pickFile(pywebview 桥 + 开发回退)
  lib/api.ts                     # MODIFY: scanProject + indexed 字段
  App.tsx                        # REWRITE: 顶栏三 tab + 路由
  components/TopBar.tsx          # CREATE: Zoom 式三 tab
  components/CreateProjectModal.tsx  # CREATE: 标题 + 选择文件夹 + 创建后自动扫描
  views/CaptureView.tsx          # CREATE: 占位
  views/ProcessIngestView.tsx    # CREATE: ➕建项目 + 待索引列表 + 重新扫描 + 外部入库占位
  views/ProjectsConversationView.tsx  # CREATE: 侧栏 + 工作区 + 对话占位
  components/ProjectSidebar.tsx  # CREATE
  components/FileList.tsx        # CREATE
  components/PendingList.tsx     # CREATE
  components/ProjectPanel.tsx    # DELETE
  components/IngestPanel.tsx     # DELETE
```

---

## Task B1: `IngestRecord` 加 `indexed` 状态 + `folder_scan` 入库方式

**Files:** Modify `backend/epictrace/models.py`, `backend/epictrace/schemas.py`; Test `backend/tests/test_models.py` (扩充)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_models.py`)

```python
def test_ingest_record_indexed_defaults_false(tmp_path):
    from epictrace.config import AppConfig
    from epictrace.db import Database
    from epictrace.models import IngestRecord, Project
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    with db.session() as s:
        proj = Project(title="P", folder_path=str(tmp_path / "P")); s.add(proj); s.flush()
        rec = IngestRecord(
            project_id=proj.id, original_filename="a.md", stored_path="/x/a.md",
            content_hash="h", size_bytes=1, mtime=1.0, ingest_method="folder_scan",
        )
        s.add(rec); s.flush()
        assert rec.indexed is False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_models.py::test_ingest_record_indexed_defaults_false -v`
Expected: FAIL — `TypeError`/`AttributeError`(无 `indexed`)

- [ ] **Step 3: 实现** — 在 `backend/epictrace/models.py` 的 `IngestRecord` 内,`created_at` 之前加一列:

```python
    indexed: Mapped[bool] = mapped_column(default=False)
```

注释把 `ingest_method` 取值说明更新为 `file_direct / drag / session / folder_scan`(仅注释,列类型不变)。

- [ ] **Step 4: 运行确认通过**(同 Step 2 命令)Expected: PASS

> **Dev DB 注意**:`create_all` 不会给已存在的表加新列。本机 dev 库 `~/.epictrace/epictrace.db` 无重要数据 —— 实现者删除它即可(`rm -f ~/.epictrace/epictrace.db`),下次启动重建。测试用每测试 tmp 库,不受影响。

- [ ] **Step 5: schemas 加字段** — `backend/epictrace/schemas.py` 的 `IngestRecordOut` 加:

```python
    indexed: bool
```

- [ ] **Step 6: 跑全套 + 提交**

Run: `cd backend && .venv/bin/pytest -q`(全绿)
```bash
git add backend/epictrace/models.py backend/epictrace/schemas.py backend/tests/test_models.py
git commit -m "feat(backend): IngestRecord 加 indexed 状态 + folder_scan 入库方式" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B2: 文件夹扫描 / 重扫 service(就地登记,套忽略规则,diff)

**Files:** Create `backend/epictrace/services/scan.py`; Test `backend/tests/test_scan_service.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_scan_service.py`

```python
from pathlib import Path
from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.services.projects import ProjectService
from epictrace.services.scan import ScanService


def _setup(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path)); db.create_all()
    proj = ProjectService(db).create(title="P", folder_path=str(tmp_path / "P"))
    return db, proj, Path(proj.folder_path)


def test_scan_registers_indexable_files_in_place(tmp_path):
    db, proj, folder = _setup(tmp_path)
    (folder / "note.md").write_text("hello virtual memory", encoding="utf-8")
    (folder / "data.bin").write_bytes(b"\x00\x01")          # 非可索引后缀 → 跳过
    (folder / "node_modules").mkdir()
    (folder / "node_modules" / "junk.js").write_text("x", encoding="utf-8")  # 忽略目录 → 跳过

    svc = ScanService(db)
    result = svc.scan_and_register(proj.id)

    assert result.added == 1
    recs = svc.list_pending(proj.id)
    assert len(recs) == 1
    r = recs[0]
    assert r.original_filename == "note.md"
    assert r.stored_path == str(folder / "note.md")   # 就地:指向原路径,未复制
    assert r.ingest_method == "folder_scan"
    assert r.indexed is False
    assert "virtual memory" in r.extracted_text


def test_rescan_only_adds_new_files(tmp_path):
    db, proj, folder = _setup(tmp_path)
    (folder / "a.md").write_text("a", encoding="utf-8")
    svc = ScanService(db)
    assert svc.scan_and_register(proj.id).added == 1
    # 再扫:无新文件
    assert svc.scan_and_register(proj.id).added == 0
    # 加一个新文件再扫
    (folder / "b.txt").write_text("b", encoding="utf-8")
    r2 = svc.scan_and_register(proj.id)
    assert r2.added == 1
    assert len(svc.list_pending(proj.id)) == 2


def test_rescan_flags_missing(tmp_path):
    db, proj, folder = _setup(tmp_path)
    f = folder / "a.md"; f.write_text("a", encoding="utf-8")
    svc = ScanService(db)
    svc.scan_and_register(proj.id)
    f.unlink()
    result = svc.scan_and_register(proj.id)
    assert result.missing == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_scan_service.py -v`
Expected: FAIL — `ModuleNotFoundError: epictrace.services.scan`

- [ ] **Step 3: 实现** `backend/epictrace/services/scan.py`

```python
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from epictrace.db import Database
from epictrace.media import get_processor
from epictrace.models import IngestRecord, Project

# 硬跳过的目录(代码仓常见噪音)+ 所有点开头隐藏目录
IGNORE_DIRS = {
    "node_modules", ".git", ".venv", "venv", "env", "__pycache__",
    "dist", "build", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
}
# 仅登记这些可索引的文本/文档/代码类型(其余如二进制、媒体先跳过)
INDEXABLE_SUFFIXES = {
    ".md", ".markdown", ".txt", ".text", ".rst",
    ".pdf", ".ppt", ".pptx", ".doc", ".docx",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".json", ".yaml", ".yml", ".toml", ".csv", ".html", ".css", ".sql",
}


@dataclass(frozen=True)
class ScanResult:
    added: int
    missing: int


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_indexable(folder: Path):
    for root, dirs, files in os.walk(folder):
        # 原地裁剪:跳过忽略目录 + 隐藏目录
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            p = Path(root) / name
            if p.suffix.lower() in INDEXABLE_SUFFIXES:
                yield p


class ScanService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def scan_and_register(self, project_id: int) -> ScanResult:
        with self._db.session() as s:
            project = s.get(Project, project_id)
            if project is None:
                raise ValueError(f"project {project_id} not found")
            folder = Path(project.folder_path)

            existing_paths = {
                r.stored_path
                for r in s.execute(
                    select(IngestRecord).where(IngestRecord.project_id == project_id)
                ).scalars()
            }

            added = 0
            for p in _iter_indexable(folder):
                sp = str(p)
                if sp in existing_paths:
                    continue
                proc = get_processor(p)
                extracted = proc.process(p).text if proc is not None else ""
                s.add(
                    IngestRecord(
                        project_id=project_id,
                        original_filename=p.name,
                        stored_path=sp,
                        content_hash=_sha256(p),
                        size_bytes=p.stat().st_size,
                        mtime=p.stat().st_mtime,
                        ingest_method="folder_scan",
                        description="",
                        extracted_text=extracted,
                        indexed=False,
                    )
                )
                added += 1

            # 检测缺失:记录指向的文件已不存在
            missing = sum(
                1
                for r in s.execute(
                    select(IngestRecord).where(IngestRecord.project_id == project_id)
                ).scalars()
                if not Path(r.stored_path).exists()
            )
            return ScanResult(added=added, missing=missing)

    def list_pending(self, project_id: int) -> list[IngestRecord]:
        with self._db.session() as s:
            rows = (
                s.execute(
                    select(IngestRecord)
                    .where(IngestRecord.project_id == project_id, IngestRecord.indexed.is_(False))
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

Run: `cd backend && .venv/bin/pytest tests/test_scan_service.py -v`
Expected: PASS(3 测试)

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/services/scan.py backend/tests/test_scan_service.py
git commit -m "feat(backend): ScanService 文件夹扫描/重扫(就地登记、套忽略规则、diff、缺失检测)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

> **已知简化**(记录,非本期阻塞):忽略规则用硬编码集合 + 后缀白名单,**未解析 `.gitignore`**(可后续用 `pathspec` 增强)。无大小上限(后缀白名单已挡掉多数大二进制)。

---

## Task B3: 扫描端点 `POST /api/projects/{id}/scan`

**Files:** Modify `backend/epictrace/schemas.py`(+ScanResultOut)、`backend/epictrace/api/routers/projects.py`; Test `backend/tests/test_api_scan.py`

- [ ] **Step 1: 写失败测试** `backend/tests/test_api_scan.py`

```python
from pathlib import Path


def test_scan_endpoint_registers_and_lists(client, tmp_path):
    folder = tmp_path / "P"
    pid = client.post("/api/projects", json={"title": "P", "folder_path": str(folder)}).json()["id"]
    (folder / "note.md").write_text("hello", encoding="utf-8")

    resp = client.post(f"/api/projects/{pid}/scan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == 1

    files = client.get(f"/api/files?project_id={pid}").json()
    assert len(files) == 1
    assert files[0]["indexed"] is False
    assert files[0]["ingest_method"] == "folder_scan"


def test_scan_unknown_project_404(client):
    assert client.post("/api/projects/99999/scan").status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_api_scan.py -v`
Expected: FAIL — 404/无路由

- [ ] **Step 3: 实现**

`backend/epictrace/schemas.py` 追加:
```python
class ScanResultOut(BaseModel):
    added: int
    missing: int
```

`backend/epictrace/api/routers/projects.py` 追加(文件顶部已 `from epictrace.api.deps import get_db` 等;新增 import `ScanResultOut`、`ScanService`、`HTTPException`、`status`):
```python
from fastapi import HTTPException, status
from epictrace.schemas import ScanResultOut
from epictrace.services.scan import ScanService


@router.post("/{project_id}/scan", response_model=ScanResultOut)
def scan_project(project_id: int, db: Database = Depends(get_db)) -> ScanResultOut:
    try:
        result = ScanService(db).scan_and_register(project_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return ScanResultOut(added=result.added, missing=result.missing)
```
(注:`projects.py` 的 router 前缀是 `/projects`,app 工厂挂 `/api`,故最终 `POST /api/projects/{id}/scan`。)

- [ ] **Step 4: 运行确认通过 + 全套**

Run: `cd backend && .venv/bin/pytest -q`(全绿)

- [ ] **Step 5: 提交**

```bash
git add backend/epictrace/schemas.py backend/epictrace/api/routers/projects.py backend/tests/test_api_scan.py
git commit -m "feat(backend): POST /api/projects/{id}/scan 端点(扫描登记+缺失计数)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B4: shell 注入原生选择器 `js_api`(pick_folder / pick_file)

**Files:** Modify `shell/run.py`

- [ ] **Step 1: 修改 `shell/run.py`** — 在现有 health-poll 版本基础上,加一个 `Api` 类并注入窗口。把 `main()` 与文件改成:

```python
"""EpicTrace 桌面外壳:后台起 uvicorn,健康检查就绪后用 pywebview 开窗;暴露原生文件对话框给前端。"""
from __future__ import annotations

import threading
import time
import urllib.request

import uvicorn
import webview

from epictrace.api.app import create_app

HOST, PORT = "127.0.0.1", 8765


class Api:
    """暴露给前端 JS 的原生能力(window.pywebview.api.*)。"""

    def __init__(self) -> None:
        self._window: webview.Window | None = None

    def set_window(self, window: "webview.Window") -> None:
        self._window = window

    def pick_folder(self) -> str | None:
        if self._window is None:
            return None
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return result[0] if result else None

    def pick_file(self) -> str | None:
        if self._window is None:
            return None
        result = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
        return result[0] if result else None


def _serve() -> None:
    try:
        uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")
    except Exception as e:
        print(f"[EpicTrace] backend failed to start: {e}", flush=True)


def _wait_until_ready(timeout: float = 15.0) -> bool:
    url = f"http://{HOST}:{PORT}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def main() -> None:
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    if not _wait_until_ready():
        print("[EpicTrace] backend not ready in time; opening window anyway.", flush=True)
    api = Api()
    window = webview.create_window(
        "EpicTrace", f"http://{HOST}:{PORT}", js_api=api, width=1100, height=750
    )
    api.set_window(window)
    webview.start()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证后端仍可导入/测试不受影响**

Run: `cd backend && .venv/bin/pytest -q`(仍全绿;run.py 不被测试导入)

- [ ] **Step 3: 提交**

```bash
git add shell/run.py
git commit -m "feat(shell): 注入 js_api 原生文件/文件夹选择器(pick_folder/pick_file)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

> GUI 行为(真正弹出 Finder)需在打包态手动验证(见最终 Verification),无法在无显示环境自动化。

---

## Task F1: 前端选择器 helper + api 扩展

**Files:** Create `frontend/src/lib/pickers.ts`; Modify `frontend/src/lib/api.ts`

- [ ] **Step 1: `frontend/src/lib/pickers.ts`**

```ts
// 原生选择器:打包态走 pywebview js_api;开发态(浏览器)回退 prompt 手填路径。
declare global {
  interface Window {
    pywebview?: { api: { pick_folder(): Promise<string | null>; pick_file(): Promise<string | null> } };
  }
}

export async function pickFolder(): Promise<string | null> {
  if (window.pywebview?.api) return window.pywebview.api.pick_folder();
  return window.prompt("(开发态)输入文件夹绝对路径:")?.trim() || null;
}

export async function pickFile(): Promise<string | null> {
  if (window.pywebview?.api) return window.pywebview.api.pick_file();
  return window.prompt("(开发态)输入文件绝对路径:")?.trim() || null;
}

export {};
```

- [ ] **Step 2: 扩展 `frontend/src/lib/api.ts`** — 给 `IngestRecord` 接口加 `indexed: boolean;`,并加扫描方法:

```ts
export interface ScanResult { added: number; missing: number; }

// 在 api 对象里追加:
  scanProject: (projectId: number) =>
    fetch(`${BASE}/api/projects/${projectId}/scan`, { method: "POST" }).then(j<ScanResult>),
```

- [ ] **Step 3: 验证构建**

Run: `cd frontend && npm run build`
Expected: 成功(类型通过)

- [ ] **Step 4: 提交**

```bash
git add frontend/src/lib/pickers.ts frontend/src/lib/api.ts
git commit -m "feat(frontend): pickers helper(pywebview 桥+开发回退)+ api scanProject/indexed" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task F2: 顶栏三 tab 外壳 + App 路由(用 frontend-design)

**Files:** Create `frontend/src/components/TopBar.tsx`; Rewrite `frontend/src/App.tsx`; Delete `frontend/src/components/ProjectPanel.tsx`、`IngestPanel.tsx`

**实现方式:用 `frontend-design` skill 产出视觉**(Zoom 式图标+文字 tab、浅色、当前 tab 高亮)。逻辑契约固定如下,视觉由 frontend-design 打磨:

- `App.tsx` 持有 `activeTab` 状态:`"capture" | "process" | "projects"`,默认 `"projects"`。渲染 `<TopBar active onChange />` + 对应 view(`CaptureView` / `ProcessIngestView` / `ProjectsConversationView`)。
- `TopBar.tsx`:三个 tab —— **采集**(capture)、**信息处理和入库**(process)、**项目与对话**(projects),各带图标(lucide:`Radio`/`Inbox`/`MessagesSquare` 之类)+ 文字,横向居中或左对齐,当前高亮。
- 删除旧 `ProjectPanel.tsx`、`IngestPanel.tsx`(其职责被三 view 取代)。

- [ ] **Step 1**(frontend-design)实现 `TopBar.tsx` + 重写 `App.tsx`,先给三个 view 建**最小占位**(下一批任务填充),确保可编译。
- [ ] **Step 2** 删除旧两面板文件。
- [ ] **Step 3** `cd frontend && npm run build` → 成功;`npm run dev` 打开能看到三 tab 可切换(占位内容)。
- [ ] **Step 4** 提交:`git commit -m "feat(frontend): 顶栏三 tab 外壳 + App 路由,移除旧两面板"`(+trailer)。

**验收:** 三 tab 渲染、可切换、当前高亮;构建通过;旧面板已删。

---

## Task F3: CreateProjectModal(共享创建弹窗,用 frontend-design)

**Files:** Create `frontend/src/components/CreateProjectModal.tsx`

**逻辑契约(固定):**
- Props:`open: boolean`、`onClose()`、`onCreated(project)`。
- 内部状态:`title`、`folderPath`、`busy`、`error`。
- UI:标题输入框;**「选择文件夹」按钮** → `await pickFolder()` → 回填显示 `folderPath`;创建按钮。
- 创建逻辑:
  ```ts
  const create = async () => {
    if (!title || !folderPath) return;
    setBusy(true); setError(null);
    try {
      const p = await api.createProject(title, folderPath);   // 接受非空文件夹
      await api.scanProject(p.id);                            // 创建后自动扫描既有内容→待索引
      onCreated(p);
      onClose();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  ```
- busy 时禁用按钮;error 以红字显示。视觉由 frontend-design 打磨(shadcn Dialog)。

- [ ] **Step 1**(frontend-design)实现 `CreateProjectModal.tsx` 按上述契约。
- [ ] **Step 2** `npm run build` 成功。
- [ ] **Step 3** 提交:`git commit -m "feat(frontend): CreateProjectModal(标题+原生选文件夹+创建后自动扫描)"`(+trailer)。

**验收:** 弹窗能开关;选择文件夹调 `pickFolder`(打包态弹 Finder / 开发态 prompt);创建后自动 `scanProject`;错误可见。

---

## Task F4: 项目与对话页(侧栏 + 工作区 + 对话占位,用 frontend-design)

**Files:** Create `frontend/src/views/ProjectsConversationView.tsx`、`components/ProjectSidebar.tsx`、`components/FileList.tsx`

**逻辑契约(固定):**
- View 持有 `projects`、`selected`、`createOpen` 状态。挂载时 `api.listProjects()`(用 `cancelled` 守卫防竞态)。
- `ProjectSidebar`:列出 projects,点选 `onSelect`;底部「➕ 新建项目」→ 打开 `CreateProjectModal`;`onCreated` 后刷新列表并选中新项目。
- 工作区(选中时):标题 + `folder_path`;`FileList`(`api.listFiles(selected.id)`,展示 `original_filename / size_bytes / ingest_method / indexed(已索引/待索引徽章) / description`);下方**对话占位**——居中输入框(禁用)+ 文案「对话功能开发中(需先建立索引)」。
- 空状态:无选中/无项目 → 居中引导建第一个项目。

- [ ] **Step 1**(frontend-design)实现三个文件(Codex 式:干净侧栏 + 工作区 + 居中对话占位)。
- [ ] **Step 2** `npm run build` 成功。
- [ ] **Step 3** 提交:`git commit -m "feat(frontend): 项目与对话页(侧栏+文件浏览+对话占位)"`(+trailer)。

**验收:** 能列项目、建项目、选项目看文件列表(含 已索引/待索引 徽章);对话区为禁用占位。

---

## Task F5: 信息处理和入库页(➕建项目 + 待索引列表 + 重新扫描 + 外部入库占位,用 frontend-design)

**Files:** Create `frontend/src/views/ProcessIngestView.tsx`、`components/PendingList.tsx`

**逻辑契约(固定):**
- 顶部操作区:**「➕ 创建项目」**(开 `CreateProjectModal`)、**「重新扫描」**(对当前所选/某项目调 `api.scanProject(id)` 后刷新)、**「外部文件入库」按钮 → 占位**(点了弹提示「整理归类 Agent 开发中(Plan 8)」,禁用态)。
- `PendingList`:跨项目汇总 `indexed === false` 的入库记录(本期可按项目逐个 `listFiles` 再过滤 `!indexed`),每项显示 项目 / 路径 / 「待索引」徽章;底部「建立索引」按钮 → **占位**(提示「索引功能开发中(Plan 2)」)。
- (可选增强,同 Plan 内)扫描结果的勾选增删。

- [ ] **Step 1**(frontend-design)实现两个文件。
- [ ] **Step 2** `npm run build` 成功。
- [ ] **Step 3** 提交:`git commit -m "feat(frontend): 信息处理和入库页(快速建项目+待索引列表+重新扫描+外部入库占位)"`(+trailer)。

**验收:** 能建项目、重新扫描使新文件出现在待索引、外部入库与建立索引为明确占位。

---

## Task F6: 采集页占位(用 frontend-design)

**Files:** Create `frontend/src/views/CaptureView.tsx`

- [ ] **Step 1**(frontend-design)实现一个清晰占位:简述「开 session 采集声音/截图/剪贴板/笔记/文件」+ 一个**禁用**的「开始 session」按钮 + 「开发中(Plan 4)」标识。
- [ ] **Step 2** `npm run build` 成功。
- [ ] **Step 3** 提交:`git commit -m "feat(frontend): 采集页占位"`(+trailer)。

---

## Task F7: 整体视觉打磨 + 端到端手测(用 frontend-design)

**Files:** 跨 `frontend/src/**`(统一风格,不改逻辑契约)

- [ ] **Step 1**(frontend-design)统一过一遍:浅色主题、间距/字号层级、空状态、按钮 busy/disabled 态、徽章样式,使三 tab 观感一致、贴近 Zoom 顶栏 + Codex 对话页。不改已固定的逻辑契约。
- [ ] **Step 2** `cd frontend && npm run build` 成功;`cd backend && .venv/bin/pytest -q` 全绿。
- [ ] **Step 3** 端到端手测(打包态):`cd frontend && npm run build && cd ../backend && .venv/bin/python ../shell/run.py` → 窗口里:
  1. 切到「项目与对话」或「信息处理和入库」→ ➕ 建项目 → 「选择文件夹」**弹出 Finder** → 选一个(可非空)文件夹 → 创建。
  2. 若选了非空文件夹,其可索引文件出现在「待索引」。
  3. 往该文件夹丢个 `.md`(Finder)→「重新扫描」→ 新文件出现在待索引。
  4. 「外部文件入库」「建立索引」点了显示明确占位提示。
  5. 采集 tab 显示占位。
- [ ] **Step 4** 提交:`git commit -m "feat(frontend): 整体视觉打磨(浅色/Zoom 顶栏/Codex 对话页)"`(+trailer)。

---

## Verification(端到端)

1. **后端全绿:** `cd backend && .venv/bin/pytest -q`(含新增 scan 测试)。
2. **前端构建:** `cd frontend && npm run build` 成功。
3. **打包态手测**(见 Task F7 Step 3):原生 Finder 选择器弹出、接受非空文件夹、扫描/重新扫描使文件进入待索引、各占位明确。
4. **代号:** 全仓库(代码/前端/shell)不出现任何前身原型代号(用 `grep -rni` 对应代号应为空)。

---

## Self-Review(本计划对 spec 的覆盖)

- ✅ 顶栏三 tab(F2)· 采集占位(F6)· 信息处理和入库(F5)· 项目与对话+Codex 式(F4)。
- ✅ 创建项目弹窗 + 原生文件夹选择器 + 接受非空 + 创建后自动扫描(F3 + B4 + B2)。
- ✅ 文件夹扫描/重新扫描、就地登记、忽略规则、缺失检测(B2/B3);`indexed` 状态(B1)。
- ✅ 外部入库走 Agent → 占位(F5);建索引/对话/采集 → 占位(F4/F5/F6)。
- ✅ 原生选择器桥 + 开发回退(B4/F1)。
- ✅ 浅色视觉打磨(F7,frontend-design)。
- ⛔ 不在本期:真实 embedding/对话/采集/外部 Agent 入库(各占位);`.gitignore` 解析(B2 简化记录);拖拽入库。
