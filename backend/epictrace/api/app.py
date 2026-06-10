from fastapi import FastAPI

from epictrace.api.routers import health


def create_app() -> FastAPI:
    app = FastAPI(title="EpicTrace")
    app.include_router(health.router, prefix="/api")
    return app
