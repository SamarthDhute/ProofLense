from typing import Optional, Tuple
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.security import (
    generate_capability_token,
    hash_capability_token,
    verify_capability_token,
)
from backend.app.models.workspace import Workspace


class WorkspaceService:
    """Service handling workspace creation, capability-token lifecycle, and verification."""

    @staticmethod
    async def create_workspace(
        db: AsyncSession, name: str
    ) -> Tuple[Workspace, str]:
        """Create a new workspace and return the persisted model along with the raw capability token."""
        raw_token = generate_capability_token()
        token_hash = hash_capability_token(raw_token)

        workspace = Workspace(name=name, capability_token_hash=token_hash)
        db.add(workspace)
        await db.commit()
        await db.refresh(workspace)

        return workspace, raw_token

    @staticmethod
    async def get_workspace(
        db: AsyncSession, workspace_id: UUID
    ) -> Optional[Workspace]:
        """Retrieve workspace by primary key."""
        result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def verify_token_for_workspace(
        db: AsyncSession, workspace_id: UUID, raw_token: str
    ) -> Optional[Workspace]:
        """Verify provided bearer token against workspace's stored hash.
        
        Returns Workspace instance if valid, None otherwise.
        """
        workspace = await WorkspaceService.get_workspace(db, workspace_id)
        if not workspace:
            return None

        if verify_capability_token(raw_token, workspace.capability_token_hash):
            return workspace
        return None

    @staticmethod
    async def get_workspace_by_token(
        db: AsyncSession, raw_token: str
    ) -> Optional[Workspace]:
        """Resolve workspace from raw capability token by checking SHA-256 hash."""
        token_hash = hash_capability_token(raw_token)
        result = await db.execute(
            select(Workspace).where(Workspace.capability_token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def rotate_token(
        db: AsyncSession, workspace: Workspace
    ) -> str:
        """Rotate capability token for a workspace, immediately invalidating the previous token."""
        new_raw_token = generate_capability_token()
        workspace.capability_token_hash = hash_capability_token(new_raw_token)
        await db.commit()
        await db.refresh(workspace)
        return new_raw_token
