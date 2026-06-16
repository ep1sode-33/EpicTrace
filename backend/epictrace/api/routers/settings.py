from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, Request

from epictrace.api.deps import get_asr_provisioner, get_provisioner
from epictrace.llm.openai_compat import OpenAICompatLLM
from epictrace.schemas import (
    AsrDeviceOut,
    AsrSettingsIn,
    AsrSettingsOut,
    AsrStatusOut,
    ExtractionSettingsIn,
    ExtractionSettingsOut,
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
    failed_stage = getattr(prov, "failed_stage", None)
    return ExtractionStatusOut(
        state=prov.state, ready=prov.is_ready(), error=error,
        failed_stage=failed_stage,
    )


@router.get("/extraction/status", response_model=ExtractionStatusOut)
def extraction_status(request: Request):
    return _provision_status(get_provisioner(request))


@router.post("/extraction/provision", response_model=ExtractionStatusOut)
def extraction_provision(request: Request):
    """触发 provisioning(后台线程,粗粒度状态)。立即返回当前状态;前端轮询 status。

    失败状态/原因(failed + last_error)由 provisioner 自身记录;重复触发由 provisioner
    内部并发守卫 no-op。这里只在后台线程吞掉上抛的异常(线程内未捕获异常无人接收)。"""
    prov = get_provisioner(request)

    def _run():
        try:
            prov.provision()
        except Exception:  # noqa: BLE001 — 失败状态/last_error 已由 provisioner 记录
            pass

    threading.Thread(target=_run, daemon=True).start()
    return _provision_status(prov)


@router.get("/extraction/settings", response_model=ExtractionSettingsOut)
def get_extraction_settings(request: Request):
    return _svc(request).get_extraction_settings()


@router.put("/extraction/settings", response_model=ExtractionSettingsOut)
def put_extraction_settings(payload: ExtractionSettingsIn, request: Request):
    try:
        return _svc(request).set_extraction_settings(
            engine=payload.engine,
            effort=payload.effort,
            model_source=payload.model_source,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/extraction/download-models", response_model=ExtractionStatusOut)
def extraction_download_models(request: Request):
    """触发模型下载(后台线程,粗粒度状态)。立即返回当前状态;前端轮询 status。

    机制同 extraction_provision:后台线程吞掉上抛异常(失败状态/last_error 由
    provisioner 记录);重复触发由 provisioner 内部并发守卫 no-op。model_source 取
    持久化设置(无 → AppConfig 默认)。"""
    prov = get_provisioner(request)
    model_source = _svc(request).get_extraction_settings()["model_source"]

    def _run():
        try:
            prov.download_models(model_source=model_source)
        except Exception:  # noqa: BLE001 — 失败状态/last_error 已由 provisioner 记录
            pass

    threading.Thread(target=_run, daemon=True).start()
    return _provision_status(prov)


# ---- ASR 设置/状态/模型下载(faster-whisper)——镜像 extraction 三件套 ----


def _asr_status(prov, model: str) -> AsrStatusOut:
    """ASR provisioner 当前状态 + 目标模型就绪与否。is_ready(model) 看的是配置里选中的
    模型(而非 provisioner 上次下载的 _last_model),前端据此显示「这个 model 是否已下」。"""
    return AsrStatusOut(
        state=prov.state, ready=prov.is_ready(model), model=model,
        error=getattr(prov, "last_error", None),
    )


@router.get("/asr/settings", response_model=AsrSettingsOut)
def get_asr_settings(request: Request):
    return _svc(request).get_asr_settings()


@router.put("/asr/settings", response_model=AsrSettingsOut)
def put_asr_settings(payload: AsrSettingsIn, request: Request):
    # 部分更新:只把显式给出(非 None)的键传给服务层合并,其余保留现状。
    sent = payload.model_dump(exclude_unset=True)
    patch = {k: v for k, v in sent.items() if v is not None}
    # input_device 例外:它的「系统默认」就是 None,用户显式选系统默认须能落库,
    # 故只要请求显式带了 input_device(即便为 null)就纳入 patch(不被上面的非 None 过滤丢掉)。
    if "input_device" in sent:
        patch["input_device"] = sent["input_device"]
    try:
        return _svc(request).set_asr_settings(patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/asr/devices", response_model=list[AsrDeviceOut])
def asr_devices():
    """枚举可用的输入设备(麦克风),供用户挑选采集输入(Feature A)。

    sounddevice 懒导入(测试/无 PortAudio 环境不应硬依赖它);任何错误(ImportError /
    PortAudio 未装 / 查询失败)都回空列表而非 500——拿不到设备表不该让设置页崩。
    只回 max_input_channels>0 的设备(输出设备不能当输入)。
    """
    try:
        import sounddevice as sd

        devices = sd.query_devices()
    except Exception:  # noqa: BLE001 — 缺 sounddevice/PortAudio 或查询失败都回空表
        return []
    out: list[dict] = []
    for i, d in enumerate(devices):
        try:
            if d.get("max_input_channels", 0) > 0:
                out.append({"index": i, "name": d.get("name", "")})
        except Exception:  # noqa: BLE001 — 单个设备项异常不拖垮整张表
            continue
    return out


@router.get("/asr/status", response_model=AsrStatusOut)
def asr_status(request: Request):
    model = _svc(request).get_asr_settings()["model"]
    return _asr_status(get_asr_provisioner(request), model)


@router.post("/asr/download-model", response_model=AsrStatusOut)
def asr_download_model(request: Request):
    """触发当前配置模型的下载(后台线程,粗粒度状态)。立即返回当前状态;前端轮询 status。

    机制同 extraction_download_models:后台线程吞掉上抛异常(失败状态/last_error 由
    provisioner 记录);下载中重复触发由 provisioner 内部并发守卫 no-op。模型取持久化
    ASR 设置(无 → AsrConfig 默认 large-v3)。"""
    prov = get_asr_provisioner(request)
    model = _svc(request).get_asr_settings()["model"]

    def _run():
        try:
            prov.download_model(model)
        except Exception:  # noqa: BLE001 — 失败状态/last_error 已由 provisioner 记录
            pass

    threading.Thread(target=_run, daemon=True).start()
    return _asr_status(prov, model)
