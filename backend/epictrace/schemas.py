from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    title: str
    folder_path: str = Field(min_length=1)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    folder_path: str
    created_at: datetime


class IngestRequest(BaseModel):
    project_id: int
    source_path: str = Field(min_length=1)
    ingest_method: Literal["file_direct", "drag", "session"] = "file_direct"
    description: str = ""


class IngestRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    original_filename: str
    stored_path: str
    content_hash: str
    size_bytes: int
    mtime: float
    ingest_method: str
    description: str
    indexed: bool
    created_at: datetime


class ScanResultOut(BaseModel):
    added: int
    missing: int


class IndexStatusOut(BaseModel):
    project_id: int
    total: int
    done: int
    status: str
    errors: list[str] = []


class ConversationCreate(BaseModel):
    title: str | None = None


class RenameIn(BaseModel):
    title: str


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    title: str
    created_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: str
    citations_json: str | None = None
    created_at: datetime


class SourceOut(BaseModel):
    filename: str
    path: str
    text: str


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str = ""
    model: str = Field(min_length=1)
    context_window: int = 32768


class ProfileUpdate(BaseModel):
    """部分更新:None/缺省 → 保留原值;尤其 api_key 缺省/空串视为「保留既有」。"""
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    context_window: int | None = None


class SetActiveIn(BaseModel):
    profile_id: str


class TestProfileIn(BaseModel):
    """测试连接:用「正在编辑的值」(尚未保存)做一次真实最小补全调用。"""
    base_url: str = Field(min_length=1)
    api_key: str = ""
    model: str = Field(min_length=1)


class TestProfileOut(BaseModel):
    """测试结果是「数据」而非 HTTP 错误:始终 200,前端据 ok 显示成功/原始错误。"""
    ok: bool
    sample: str | None = None
    error: str | None = None


class ProfileView(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    context_window: int
    api_key_set: bool


class SettingsOut(BaseModel):
    configured: bool
    active_profile_id: str | None
    profiles: list[ProfileView]


class ExtractionSettingsIn(BaseModel):
    # 默认须与服务默认(pypdf)一致:省略 engine 的部分 PUT 不应静默切到 MinerU。
    engine: str = "pypdf"
    effort: str
    model_source: str


class ExtractionSettingsOut(BaseModel):
    engine: str
    effort: str
    model_source: str


class ExtractionStatusOut(BaseModel):
    # not_installed | installing | installed_no_models | downloading_models | ready | failed
    state: str
    ready: bool
    error: str | None = None
    # install | download | None —— 区分装包失败与下模型失败,前端据此把「重试」指向正确动作。
    # 即便 cached 模型仍可用(state==ready),一次失败的重下也经此 + error 暴露。
    failed_stage: str | None = None


class AsrSettingsIn(BaseModel):
    """ASR 可调配置的部分更新:仅给出的键被覆盖,其余保留现状(服务层合并 + 校验)。
    全字段可选,使「只改一个旋钮」(如 model)的 PUT 不会把其余键重置为默认。"""
    model: str | None = None
    language: str | None = None
    vad: bool | None = None
    vad_threshold: float | None = None
    vad_min_speech_ms: int | None = None
    no_speech: float | None = None
    log_prob: float | None = None
    compression_ratio: float | None = None
    repetition_penalty: float | None = None
    no_repeat_ngram: int | None = None
    condition_prev: bool | None = None
    halluc_silence: float | None = None
    force_confirm_after: int | None = None
    stall_seek_seconds: float | None = None
    rms_normalize: bool | None = None
    halluc_filter_enabled: bool | None = None
    input_device: int | None = None
    window_seconds: float | None = None
    chunk_seconds: float | None = None
    slice_padding: float | None = None
    max_slice: float | None = None
    anchor_words: int | None = None
    compute_type: str | None = None


class AsrSettingsOut(BaseModel):
    """完整 ASR 配置(AsrConfig.to_dict 的形状)。"""
    model: str
    language: str
    vad: bool
    vad_threshold: float
    vad_min_speech_ms: int
    no_speech: float
    log_prob: float
    compression_ratio: float
    repetition_penalty: float
    no_repeat_ngram: int
    condition_prev: bool
    halluc_silence: float | None = None
    force_confirm_after: int
    stall_seek_seconds: float
    rms_normalize: bool
    halluc_filter_enabled: bool
    input_device: int | None = None
    window_seconds: float
    chunk_seconds: float
    slice_padding: float
    max_slice: float
    anchor_words: int
    compute_type: str


class AsrDeviceOut(BaseModel):
    """一个输入设备(麦克风)。index = sounddevice 设备索引,持久化进 input_device。"""
    index: int
    name: str


class AsrStatusOut(BaseModel):
    # not_downloaded | downloading | ready | failed
    state: str
    ready: bool
    model: str
    error: str | None = None


class ReferenceCreate(BaseModel):
    kind: Literal["external", "internal"]
    source_path: str | None = None       # external 必填
    ingest_record_id: int | None = None  # internal 必填


class ReferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    conversation_id: int
    kind: str
    display_name: str
    source_path: str | None = None
    ingest_record_id: int | None = None
    mode: str
    text_chars: int
    detached: bool
    created_at: datetime


class CaptureEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    ts: datetime
    payload: str
    meta: dict


class CaptureSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    sources: list[str]
    staging_dir: str  # 内部路径:前端不展示,仅转发给 native.startMonitors 供 shell 存截图


class CaptureSessionDetailOut(CaptureSessionOut):
    events: list[CaptureEventOut] = []
    elapsed_seconds: float = 0.0
    # 会话停止后正在整文件重转(权威转录尚未到达);暂存区据此显示「重新转写中…」。
    retranscribing: bool = False


class StartSessionIn(BaseModel):
    sources: list[str]


class AppendEventIn(BaseModel):
    kind: str
    payload: str = ""
    meta: dict = {}


class OrganizeIn(BaseModel):
    project_id: int


class PartialIn(BaseModel):
    source: str  # "mic" | "device"
    text: str


class TranscriptReplaceIn(BaseModel):
    """权威重转结果:替换某 session 全部转录事件。segments 为松散 dict(含 source/text/start/end/
    audio_offset/words/wav),由 retranscribe 子进程回写。"""
    segments: list[dict] = []


class AsrSourceIn(BaseModel):
    """启停某路音频源:source 为前端源 id("mic"|"system_audio"),enabled=True 开启 / False 关闭。

    取代旧的软静音切换:开关现在是「真·启停该音源」——开启会(必要时懒启动 worker 并)开麦/起
    helper 开始采集转写,关闭会停掉采集。中途也能开启开始没勾的源。"""
    source: str
    enabled: bool
