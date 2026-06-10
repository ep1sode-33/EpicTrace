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

    app.include_router(health.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(files.router, prefix="/api")
    return app
