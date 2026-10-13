from uuid import UUID
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.deps import get_current_workspace
from backend.app.core.database import get_db
from backend.app.models.workspace import Workspace
from backend.app.schemas.workspace import (
    TokenRotateResponse,
    WorkspaceCreate,
    WorkspaceCreateResponse,
    WorkspaceResponse,
)
from backend.app.services.workspace_service import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


@router.post(
    "",
    response_model=WorkspaceCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new isolated workspace",
)
async def create_workspace(
    payload: WorkspaceCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new workspace and return the newly generated capability token.
    
    CRITICAL: The raw capability token is returned ONLY in this response.
    It cannot be retrieved again; store it securely in client storage.
    """
    workspace, raw_token = await WorkspaceService.create_workspace(db, payload.name)
    return WorkspaceCreateResponse(
        id=workspace.id,
        name=workspace.name,
        capability_token=raw_token,
        created_at=workspace.created_at,
    )


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_200_OK,
    summary="Get workspace details by ID",
)
async def get_workspace(
    workspace_id: UUID,
    workspace: Workspace = Depends(get_current_workspace),
):
    """Retrieve workspace metadata. Requires valid Bearer capability token in Authorization header."""
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        created_at=workspace.created_at,
    )


@router.post(
    "/{workspace_id}/token/rotate",
    response_model=TokenRotateResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate workspace capability token",
)
async def rotate_token(
    workspace_id: UUID,
    workspace: Workspace = Depends(get_current_workspace),
    db: AsyncSession = Depends(get_db),
):
    """Rotate the capability token for the workspace.
    
    Requires current valid Bearer token.
    Immediately invalidates the previous token and returns the new token.
    """
    new_token = await WorkspaceService.rotate_token(db, workspace)
    return TokenRotateResponse(
        id=workspace.id,
        capability_token=new_token,
    )
