from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from epictrace.config import AppConfig
from epictrace.db import Database
from epictrace.api.routers import files, health, projects


def create_app(db: Database | None = None, embedder=None, vector_store=None) -> FastAPI:
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
        db = Database(AppConfig())
        db.create_all()
    app.state.db = db
    # embedder/vector_store 可注入(测试注入假件)。默认延迟构造:不在 create_app 里
    # 急切起 BGE-M3 / Milvus(那样会拖慢/污染 health/projects/files 等无关用例),
    # 而是首次用到索引路由时再建真件(见 deps.get_embedder / get_vector_store)。
    app.state.embedder = embedder
    app.state.vector_store = vector_store
    app.state.index_jobs = {}  # project_id -> IndexJob(最近一次)

    app.include_router(health.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(files.router, prefix="/api")

    import os
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles

    dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app
