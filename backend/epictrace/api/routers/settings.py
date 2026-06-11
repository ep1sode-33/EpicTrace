from __future__ import annotations

from fastapi import APIRouter, Request

from epictrace.schemas import SettingsIn
from epictrace.services.settings import SettingsService

router = APIRouter(tags=["settings"])  # /api 由 app 工厂统一挂载


# 用 app.state.config(create_app 注入,测试为 tmp data_dir)而非新建 AppConfig(),保证隔离。
@router.get("/settings")
def get_settings(request: Request):
    return SettingsService(request.app.state.config).public_view()


@router.put("/settings")
def put_settings(payload: SettingsIn, request: Request):
    svc = SettingsService(request.app.state.config)
    svc.update_chat_llm(
        base_url=payload.chat_llm.base_url,
        api_key=payload.chat_llm.api_key,
        model=payload.chat_llm.model,
    )
    request.app.state.llm = None  # 失效缓存,下次按新设置重建
    return svc.public_view()
