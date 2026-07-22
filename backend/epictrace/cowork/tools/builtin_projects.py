"""项目写操作工具(需求 3 工具清单):create_project / add_file_to_project / rebuild_index。

复用服务层(ProjectService / IngestService / IndexService),与 projects 路由同一条路径:
- IndexService 的 vector_store 传 getter 延迟构造(gRPC/fork 顺序护栏);
- rebuild_index 复用 app.state.index_lock + index_jobs,前端轮询 index/status 可见进度。
写操作全部 permission=ask。
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from pathlib import Path

from epictrace.db import Database
from epictrace.services.index import IndexService
from epictrace.services.ingest import IngestService
from epictrace.services.projects import ProjectService
from epictrace.cowork.tools.builtin_fs import _project
from epictrace.cowork.tools.registry import ToolDef


def _slug(title: str) -> str:
    s = re.sub(r"[^\w一-鿿-]+", "-", title.strip()).strip("-").lower()
    return s or "project"


def build_project_tools(
    db: Database,
    *,
    get_embedder: Callable,
    get_vector_store: Callable,
    index_jobs: dict,
    index_lock: threading.Lock,
    get_provisioner: Callable | None = None,
) -> list[ToolDef]:

    def create_project(title: str, folder_path: str = "") -> str:
        title = title.strip()
        if not title:
            return "Error: title 不能为空"
        folder = folder_path.strip() or str(Path(db.config.data_dir) / "projects" / _slug(title))
        try:
            p = ProjectService(db).create(title=title, folder_path=folder)
        except Exception as e:  # noqa: BLE001 — 建目录/写库失败回传
            return f"Error: 创建项目失败({type(e).__name__}: {e})"
        return f"已创建项目:id={p.id} | {p.title} | {p.folder_path}"

    def add_file_to_project(project_id: int, source_path: str, description: str = "") -> str:
        if _project(db, project_id) is None:
            return f"Error: project {project_id} not found"
        src = Path(source_path).expanduser()
        if not src.is_file():
            return f"Error: 文件不存在:{source_path}"
        try:
            rec = IngestService(db).ingest_file(
                project_id, str(src), ingest_method="cowork",
                description=description or src.name)
        except Exception as e:  # noqa: BLE001
            return f"Error: 添加文件失败({type(e).__name__}: {e})"
        return (f"已添加 {src.name} 到项目 {project_id}(record id={rec.id})。"
                "文本已提取;要做语义检索需先 rebuild_index 建/更新向量索引。")

    def rebuild_index(project_id: int) -> str:
        proj = _project(db, project_id)
        if proj is None:
            return f"Error: project {project_id} not found"
        svc = IndexService(db, get_embedder(), get_vector_store,
                           provisioner=get_provisioner() if get_provisioner else None)
        with index_lock:
            running = index_jobs.get(project_id)
            if running is not None and running.status == "running":
                return (f"项目 {project_id} 的索引任务正在运行中"
                        f"({running.done}/{running.total}),不重复启动。")
            try:
                job = svc.index_project(project_id)
            except Exception as e:  # noqa: BLE001
                return f"Error: 启动索引失败({type(e).__name__}: {e})"
            index_jobs[project_id] = job
            svc.run_in_background(job)
        return (f"已为项目「{proj.title}」启动索引(共 {job.total} 个文件待处理,"
                "后台运行)。完成后 search_vector/search_hybrid 即可检索新内容。")

    return [
        ToolDef(
            name="create_project",
            description=(
                "创建新项目(在数据目录下建同名文件夹,或指定 folder_path)。"
                "返回新项目的 id,后续读文件/检索都用它。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "项目标题"},
                    "folder_path": {"type": "string",
                                    "description": "可选,自定义项目文件夹绝对路径;默认在数据目录下创建"},
                },
                "required": ["title"],
            },
            handler=create_project,
            permission="ask",
        ),
        ToolDef(
            name="add_file_to_project",
            description=(
                "把主机上的一个文件复制进项目并提取文本(source_path 为绝对路径)。"
                "添加后需 rebuild_index 才能被语义检索到。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "source_path": {"type": "string", "description": "源文件绝对路径"},
                    "description": {"type": "string", "description": "可选说明"},
                },
                "required": ["project_id", "source_path"],
            },
            handler=add_file_to_project,
            permission="ask",
        ),
        ToolDef(
            name="rebuild_index",
            description=(
                "为项目构建/更新向量索引(后台运行;只处理未索引的记录,"
                "不会清空已有索引)。添加新文件后调用,语义检索才能命中新内容。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                },
                "required": ["project_id"],
            },
            handler=rebuild_index,
            permission="ask",
        ),
    ]
