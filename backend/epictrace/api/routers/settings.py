from __future__ import annotations

from fastapi import APIRouter, Request

from epictrace.schemas import ProfileCreate, ProfileUpdate, SetActiveIn
from epictrace.services.settings import SettingsService

router = APIRouter(tags=["settings"])  # /api 由 app 工厂统一挂载


# 用 app.state.config(create_app 注入,测试为 tmp data_dir)而非新建 AppConfig(),保证隔离。
def _svc(request: Request) -> SettingsService:
    return SettingsService(request.app.state.config)


def _invalidate_llm(request: Request) -> None:
    """任一变更后失效缓存的 LLM,下次按(可能切换的)活动 Profile 重建。"""
    request.app.state.llm = None


@router.get("/settings")
def get_settings(request: Request):
    return _svc(request).public_view()


@router.post("/settings/profiles")
def create_profile(payload: ProfileCreate, request: Request):
    svc = _svc(request)
    svc.create_profile(
        name=payload.name,
        base_url=payload.base_url,
        api_key=payload.api_key,
        model=payload.model,
    )
    _invalidate_llm(request)
    return svc.public_view()


@router.put("/settings/profiles/{profile_id}")
def update_profile(profile_id: str, payload: ProfileUpdate, request: Request):
    svc = _svc(request)
    # 前端只拿打码视图(api_key_set):编辑时留空 api_key 会回传空串/缺省 → 视为「保留既有」。
    # 仅当用户填了非空新 key 才覆盖。
    api_key = payload.api_key if payload.api_key else None
    svc.update_profile(
        profile_id,
        name=payload.name,
        base_url=payload.base_url,
        model=payload.model,
        api_key=api_key,
    )
    _invalidate_llm(request)
    return svc.public_view()


@router.delete("/settings/profiles/{profile_id}")
def delete_profile(profile_id: str, request: Request):
    svc = _svc(request)
    svc.delete_profile(profile_id)
    _invalidate_llm(request)
    return svc.public_view()


@router.put("/settings/active")
def set_active(payload: SetActiveIn, request: Request):
    svc = _svc(request)
    svc.set_active(payload.profile_id)
    _invalidate_llm(request)
    return svc.public_view()
