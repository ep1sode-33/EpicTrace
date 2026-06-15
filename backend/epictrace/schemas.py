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


class ExtractionStatusOut(BaseModel):
    state: str            # not_installed | installing | ready | failed
    ready: bool
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
