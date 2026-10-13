from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Workspace display name")

    model_config = ConfigDict(from_attributes=True)


class WorkspaceCreateResponse(BaseModel):
    id: UUID
    name: str
    capability_token: str = Field(
        ...,
        description="High-entropy Bearer token. Store securely; returned ONLY upon workspace creation.",
    )
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenRotateResponse(BaseModel):
    id: UUID
    capability_token: str = Field(
        ...,
        description="Newly generated high-entropy Bearer token. Previous token is permanently invalidated.",
    )
    message: str = "Capability token rotated successfully. Previous token has been invalidated."

    model_config = ConfigDict(from_attributes=True)
