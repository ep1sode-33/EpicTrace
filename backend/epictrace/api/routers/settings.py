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
    # 前端只拿得到打码视图(api_key_set),编辑时若未重填 key 会回传空串;空串视为"不改 key"
    # → 传 None 让 service 保留已存 key,避免一存设置就把真 key 抹掉。
    incoming_key = payload.chat_llm.api_key
    svc.update_chat_llm(
        base_url=payload.chat_llm.base_url,
        model=payload.chat_llm.model,
        api_key=incoming_key if incoming_key else None,
    )
    request.app.state.llm = None  # 失效缓存,下次按新设置重建
    return svc.public_view()
