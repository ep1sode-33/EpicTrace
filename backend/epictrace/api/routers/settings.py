from __future__ import annotations

import threading

from fastapi import APIRouter, Request

from epictrace.api.deps import get_provisioner
from epictrace.llm.openai_compat import OpenAICompatLLM
from epictrace.schemas import (
    ExtractionStatusOut,
    ProfileCreate,
    ProfileUpdate,
    SetActiveIn,
    TestProfileIn,
    TestProfileOut,
)
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
        context_window=payload.context_window,
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
        context_window=payload.context_window,
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


@router.post("/settings/test")
def test_profile(payload: TestProfileIn) -> TestProfileOut:
    """对「正在编辑的值」发一次真实最小补全:这是唯一能验证 OpenAI-compat 端点的方式。
    失败是数据而非 HTTP 错误,始终 200,让前端展示网关原始报错。"""
    try:
        llm = OpenAICompatLLM(payload.base_url, payload.api_key, payload.model)
        text = llm.complete([{"role": "user", "content": "ping"}], max_tokens=16)
        return TestProfileOut(ok=True, sample=(text or "").strip()[:80])
    except Exception as exc:  # 任何异常(网络/鉴权/4xx/超时…)都回传原始信息
        return TestProfileOut(ok=False, error=str(exc)[:400])


def _provision_status(prov) -> ExtractionStatusOut:
    error = getattr(prov, "last_error", None)
    return ExtractionStatusOut(state=prov.state, ready=prov.is_ready(), error=error)


@router.get("/extraction/status", response_model=ExtractionStatusOut)
def extraction_status(request: Request):
    return _provision_status(get_provisioner(request))


@router.post("/extraction/provision", response_model=ExtractionStatusOut)
def extraction_provision(request: Request):
    """触发 provisioning(后台线程,粗粒度状态)。立即返回当前状态;前端轮询 status。"""
    prov = get_provisioner(request)

    def _run():
        try:
            prov.provision()
        except Exception as exc:  # noqa: BLE001 — 失败状态由 prov.state 反映;记录原因
            try:
                prov.last_error = str(exc)[:500]
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_run, daemon=True).start()
    return _provision_status(prov)
