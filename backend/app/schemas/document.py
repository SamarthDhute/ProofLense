from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class DocumentBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DocumentUploadResponse(DocumentBase):
    id: UUID
    workspace_id: UUID
    filename: str
    file_hash: str
    mime_type: str
    status: str
    created_at: datetime
    updated_at: datetime


class DocumentListItem(DocumentBase):
    id: UUID
    workspace_id: UUID
    filename: str
    file_hash: str
    mime_type: str
    status: str
    page_count: int = 0
    chunk_count: int = 0
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    items: List[DocumentListItem]
    total: int
    skip: int
    limit: int


class DocumentDetailResponse(DocumentBase):
    id: UUID
    workspace_id: UUID
    filename: str
    file_hash: str
    mime_type: str
    status: str
    page_count: int = 0
    chunk_count: int = 0
    error_message: Optional[str] = None
    processing_heartbeat: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class DocumentDeleteResponse(BaseModel):
    id: UUID
    deleted: bool
    message: str


class RecoveryItemResult(BaseModel):
    document_id: UUID
    action: str
    status: str
    error: Optional[str] = None


class RecoveryResponse(BaseModel):
    recovered_count: int
    permanently_failed_count: int
    results: List[RecoveryItemResult]
