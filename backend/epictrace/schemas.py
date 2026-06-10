from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    title: str
    folder_path: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    folder_path: str
    created_at: datetime


class IngestRequest(BaseModel):
    project_id: int
    source_path: str
    ingest_method: str = "file_direct"
    description: str = ""


class IngestRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    original_filename: str
    stored_path: str
    content_hash: str
    size_bytes: int
    ingest_method: str
    description: str
    created_at: datetime
