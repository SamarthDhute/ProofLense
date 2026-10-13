from typing import Optional
from uuid import UUID
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models.workspace import Workspace
from backend.app.services.workspace_service import WorkspaceService
from backend.app.storage.base import StorageService
from backend.app.storage.local import LocalStorageService
from backend.app.storage.supabase import SupabaseStorageService


_storage_instance: Optional[StorageService] = None


def get_storage_service() -> StorageService:
    """Dependency provider for storage service singleton."""
    global _storage_instance
    if _storage_instance is None:
        if settings.STORAGE_BACKEND == "supabase" and settings.SUPABASE_SERVICE_ROLE_KEY:
            _storage_instance = SupabaseStorageService(
                supabase_url=settings.SUPABASE_URL,
                service_role_key=settings.SUPABASE_SERVICE_ROLE_KEY,
                bucket=settings.SUPABASE_STORAGE_BUCKET,
            )
        else:
            _storage_instance = LocalStorageService(settings.STORAGE_LOCAL_ROOT)
    return _storage_instance


async def get_current_workspace(
    workspace_id: UUID,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> Workspace:
    """Validate Bearer capability token and enforce strict workspace tenancy."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Bearer capability token required.",
        )

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected: 'Bearer <token>'.",
        )

    raw_token = parts[1]

    # Check if workspace exists
    workspace = await WorkspaceService.get_workspace(db, workspace_id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found.",
        )

    # Verify cryptographic capability token
    authenticated = await WorkspaceService.verify_token_for_workspace(
        db, workspace_id, raw_token
    )
    if not authenticated:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or revoked capability token for this workspace.",
        )

    return workspace
