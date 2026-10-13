import hashlib
import re
from typing import Optional, Tuple
from uuid import UUID
import httpx

from backend.app.storage.base import StorageService
from backend.app.storage.local import StorageSecurityError


class SupabaseStorageService(StorageService):
    """Production-grade Supabase Cloud Storage implementation for PDF documents.
    
    Stores files in a private Supabase Storage bucket under the canonical hierarchy:
    `workspaces/{workspace_id}/raw/{document_id}{extension}`.
    Provides atomic SHA-256 computation, path sanitization, and signed download URLs.
    """

    def __init__(
        self,
        supabase_url: str,
        service_role_key: str,
        bucket: str = "prooflens-documents",
        timeout: float = 30.0,
    ):
        clean_url = supabase_url.rstrip("/")
        self.supabase_url = clean_url
        self.service_role_key = service_role_key
        self.bucket = bucket
        self.timeout = timeout
        self.api_base = f"{clean_url}/storage/v1"
        self._headers = {
            "Authorization": f"Bearer {service_role_key}",
            "apikey": service_role_key,
        }

    def _sanitize_path(self, path: str) -> str:
        """Sanitize object path against directory traversal and illegal characters."""
        if not path or not isinstance(path, str):
            raise StorageSecurityError("Storage path must be a non-empty string.")
        # Normalize slashes
        clean = path.replace("\\", "/").strip("/")
        parts = clean.split("/")
        if ".." in parts or "." in parts:
            raise StorageSecurityError(f"Illegal path traversal sequence in path: {path}")
        # Allow alphanumeric, hyphens, underscores, slashes, and period for extension
        if not re.match(r"^[a-zA-Z0-9_\-\./]+$", clean):
            raise StorageSecurityError(f"Illegal characters in storage path: {path}")
        return clean

    async def save_file(
        self,
        workspace_id: UUID,
        document_id: UUID,
        file_data: bytes,
        extension: str = ".pdf",
    ) -> Tuple[str, str, int]:
        """Upload file bytes to Supabase Storage bucket atomically with SHA-256 calculation."""
        canonical_path = f"workspaces/{workspace_id}/raw/{document_id}{extension}"
        safe_path = self._sanitize_path(canonical_path)

        sha256 = hashlib.sha256(file_data).hexdigest()
        file_size = len(file_data)

        content_type = "application/pdf" if extension.lower() == ".pdf" else "application/octet-stream"
        upload_headers = {
            **self._headers,
            "Content-Type": content_type,
            "x-upsert": "true",
        }

        url = f"{self.api_base}/object/{self.bucket}/{safe_path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=upload_headers, content=file_data)
            if response.status_code not in (200, 201):
                raise RuntimeError(
                    f"Failed to upload file to Supabase Storage: HTTP {response.status_code} - {response.text}"
                )

        return safe_path, sha256, file_size

    async def get_file_bytes(self, storage_path: str) -> bytes:
        """Retrieve full byte content of a stored file from Supabase Storage."""
        safe_path = self._sanitize_path(storage_path)
        url = f"{self.api_base}/object/authenticated/{self.bucket}/{safe_path}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=self._headers)
            if response.status_code == 404 or (
                response.status_code == 400 and ("not_found" in response.text or "NoSuchKey" in response.text)
            ):
                raise FileNotFoundError(f"Stored file not found in Supabase Storage: {storage_path}")
            if response.status_code != 200:
                raise RuntimeError(
                    f"Failed to fetch file from Supabase Storage: HTTP {response.status_code} - {response.text}"
                )
            return response.content

    async def delete_file(self, storage_path: str) -> bool:
        """Permanently delete file from Supabase Storage. Idempotent operation."""
        try:
            safe_path = self._sanitize_path(storage_path)
        except StorageSecurityError:
            return True

        url = f"{self.api_base}/object/{self.bucket}/{safe_path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(url, headers=self._headers)
            # 200 or 404 are both considered successful idempotent deletion
            if response.status_code in (200, 204, 404):
                return True
            return False

    def file_exists(self, storage_path: str) -> bool:
        """Check if file exists in Supabase Storage synchronously."""
        try:
            safe_path = self._sanitize_path(storage_path)
            url = f"{self.api_base}/object/info/authenticated/{self.bucket}/{safe_path}"
            with httpx.Client(timeout=min(self.timeout, 5.0)) as client:
                res = client.get(url, headers=self._headers)
                return res.status_code == 200
        except Exception:
            return False

    def get_file_size(self, storage_path: str) -> Optional[int]:
        """Return file size in bytes from Supabase Storage metadata."""
        try:
            safe_path = self._sanitize_path(storage_path)
            url = f"{self.api_base}/object/info/authenticated/{self.bucket}/{safe_path}"
            with httpx.Client(timeout=min(self.timeout, 5.0)) as client:
                res = client.get(url, headers=self._headers)
                if res.status_code == 200:
                    data = res.json()
                    return data.get("size") or data.get("content_length")
            return None
        except Exception:
            return None

    async def create_signed_url(self, storage_path: str, expires_in_seconds: int = 3600) -> str:
        """Generate a time-limited signed URL for secure direct client download."""
        safe_path = self._sanitize_path(storage_path)
        url = f"{self.api_base}/object/sign/{self.bucket}/{safe_path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(url, headers=self._headers, json={"expiresIn": expires_in_seconds})
            if res.status_code != 200:
                raise RuntimeError(f"Failed to generate signed URL: HTTP {res.status_code} - {res.text}")
            signed_url_path = res.json().get("signedURL")
            if not signed_url_path:
                raise RuntimeError("Supabase returned empty signedURL path.")
            return f"{self.supabase_url}/storage/v1{signed_url_path}"

    async def check_bucket_health(self) -> bool:
        """Verify storage bucket accessibility and permission."""
        try:
            url = f"{self.api_base}/bucket/{self.bucket}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url, headers=self._headers)
                return res.status_code == 200
        except Exception:
            return False
