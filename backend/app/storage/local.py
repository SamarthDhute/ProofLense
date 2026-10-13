import hashlib
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple
from uuid import UUID

from backend.app.storage.base import StorageService


class StorageSecurityError(Exception):
    """Raised when an illegal or path-traversing file operation is detected."""
    pass


class LocalStorageService(StorageService):
    """Local filesystem storage service adhering to strict workspace isolation and atomic writes."""

    def __init__(self, base_dir: str):
        self.base_dir = os.path.realpath(os.path.abspath(base_dir))
        os.makedirs(self.base_dir, exist_ok=True)

    def _resolve_safe_path(self, relative_path: str) -> str:
        """Resolve and validate path against path traversal attacks."""
        clean_rel = os.path.normpath(relative_path).lstrip("/\\")
        candidate = os.path.realpath(os.path.join(self.base_dir, clean_rel))
        if not candidate.startswith(self.base_dir):
            raise StorageSecurityError(f"Path traversal attempt detected: {relative_path}")
        return candidate

    async def save_file(
        self,
        workspace_id: UUID,
        document_id: UUID,
        file_data: bytes,
        extension: str = ".pdf",
    ) -> Tuple[str, str, int]:
        """Save file bytes atomically with SHA-256 computation."""
        # Canonical layout: workspaces/{workspace_id}/raw/{document_id}{extension}
        rel_dir = os.path.join("workspaces", str(workspace_id), "raw")
        filename = f"{document_id}{extension}"
        rel_path = os.path.join(rel_dir, filename)

        target_path = self._resolve_safe_path(rel_path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        sha256 = hashlib.sha256(file_data).hexdigest()
        file_size = len(file_data)

        # Atomic write pattern: write to temporary sibling file, then atomic rename
        target_dir = os.path.dirname(target_path)
        with tempfile.NamedTemporaryFile(dir=target_dir, delete=False) as tmp_file:
            tmp_path = tmp_file.name
            tmp_file.write(file_data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())

        os.replace(tmp_path, target_path)

        # Store relative canonical path for portability across environments
        canonical_rel_path = rel_path.replace("\\", "/")
        return canonical_rel_path, sha256, file_size

    async def get_file_bytes(self, storage_path: str) -> bytes:
        """Read and return full file bytes."""
        full_path = self._resolve_safe_path(storage_path)
        if not os.path.isfile(full_path):
            raise FileNotFoundError(f"Stored file not found: {storage_path}")
        with open(full_path, "rb") as f:
            return f.read()

    async def delete_file(self, storage_path: str) -> bool:
        """Permanently delete file from local storage."""
        try:
            full_path = self._resolve_safe_path(storage_path)
            if os.path.isfile(full_path):
                os.remove(full_path)
            return True
        except (FileNotFoundError, StorageSecurityError):
            return True
        except Exception:
            return False

    def file_exists(self, storage_path: str) -> bool:
        """Check if file exists on disk."""
        try:
            full_path = self._resolve_safe_path(storage_path)
            return os.path.isfile(full_path)
        except Exception:
            return False

    def get_file_size(self, storage_path: str) -> Optional[int]:
        """Return size in bytes or None if not found."""
        try:
            full_path = self._resolve_safe_path(storage_path)
            if os.path.isfile(full_path):
                return os.path.getsize(full_path)
            return None
        except Exception:
            return None
