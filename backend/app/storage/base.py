from abc import ABC, abstractmethod
from typing import BinaryIO, Optional, Tuple
from uuid import UUID


class StorageService(ABC):
    """Abstract storage interface isolating file operations from underlying storage medium."""

    @abstractmethod
    async def save_file(
        self,
        workspace_id: UUID,
        document_id: UUID,
        file_data: bytes,
        extension: str = ".pdf",
    ) -> Tuple[str, str, int]:
        """Save file bytes atomically.
        
        Returns:
            Tuple of (relative_or_canonical_storage_path, sha256_hash, file_size_bytes)
        """
        pass

    @abstractmethod
    async def get_file_bytes(self, storage_path: str) -> bytes:
        """Retrieve full byte content of a stored file."""
        pass

    @abstractmethod
    async def delete_file(self, storage_path: str) -> bool:
        """Permanently delete file from storage. Returns True if deleted or did not exist."""
        pass

    @abstractmethod
    def file_exists(self, storage_path: str) -> bool:
        """Check if file exists at specified path."""
        pass

    @abstractmethod
    def get_file_size(self, storage_path: str) -> Optional[int]:
        """Return file size in bytes if file exists."""
        pass
