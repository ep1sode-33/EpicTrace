from __future__ import annotations

import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from epictrace.config import AppConfig
from epictrace.db import Database
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


def create_app(
    db: Database | None = None,
    embedder=None,
    vector_store=None,
    reranker=None,
    llm=None,
    retriever=None,
    attachment_store=None,
    config: AppConfig | None = None,
) -> FastAPI:
    app = FastAPI(title="EpicTrace")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8765",
            "http://127.0.0.1:8765",
        ],  # Vite dev server + pywebview
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if db is None:
        db = Database(config or AppConfig())
        db.create_all()
    app.state.db = db
    # settings / get_llm 都读 app.state.config(而非新建 AppConfig()),保证 tmp data_dir
    # 测试隔离:优先用显式 config 参数,否则取构造 db 时的 AppConfig。
    app.state.config = config or getattr(db, "config", None) or AppConfig()
    # embedder/vector_store 可注入(测试注入假件)。默认延迟构造:不在 create_app 里
    # 急切起 BGE-M3 / Milvus(那样会拖慢/污染 health/projects/files 等无关用例),
    # 而是首次用到索引路由时再建真件(见 deps.get_embedder / get_vector_store)。
    app.state.embedder = embedder
    app.state.vector_store = vector_store
    # 会话级临时附件 store(attachment_chunks collection)。注入或延迟构造(见 deps.get_attachment_store)。
    app.state.attachment_store = attachment_store
    app.state.reranker = reranker  # 注入或延迟构造(见 deps.get_reranker)
    app.state.llm = llm  # 注入或由 SettingsService 接线(见 deps.get_llm)
    app.state.retriever = retriever  # 注入或延迟构造(见 deps.get_retriever)
    app.state.index_jobs = {}  # project_id -> IndexJob(最近一次)
    # 守护 index/reindex 的「检查在跑 + 启动新 job」临界区:双击/重试/正在跑时再点
    # 不应起第二个并发的(破坏性)重建。见 routers/projects.py。
    app.state.index_lock = threading.Lock()

    app.include_router(health.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(files.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(source.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    app.include_router(references.router, prefix="/api")
    app.include_router(capture.router, prefix="/api")

    import os
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles

    dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app
