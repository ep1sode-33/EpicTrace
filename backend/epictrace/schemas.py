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


class ChatLLMIn(BaseModel):
    base_url: str
    api_key: str = ""
    model: str


class SettingsIn(BaseModel):
    chat_llm: ChatLLMIn


class ChatLLMView(BaseModel):
    base_url: str
    model: str
    api_key_set: bool


class SettingsOut(BaseModel):
    chat_llm: ChatLLMView
