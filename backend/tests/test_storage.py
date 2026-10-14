import hashlib
import uuid
import pytest

from backend.app.storage.local import LocalStorageService, StorageSecurityError


@pytest.mark.asyncio
async def test_save_and_retrieve_file(temp_storage: LocalStorageService):
    """Test saving a file atomically and retrieving its content."""
    workspace_id = uuid.uuid4()
    document_id = uuid.uuid4()
    dummy_pdf_content = b"%PDF-1.4 sample content for prooflens testing"

    expected_sha256 = hashlib.sha256(dummy_pdf_content).hexdigest()

    rel_path, sha256_hash, file_size = await temp_storage.save_file(
        workspace_id=workspace_id,
        document_id=document_id,
        file_data=dummy_pdf_content,
        extension=".pdf",
    )

    assert sha256_hash == expected_sha256
    assert file_size == len(dummy_pdf_content)
    assert temp_storage.file_exists(rel_path) is True
    assert temp_storage.get_file_size(rel_path) == len(dummy_pdf_content)

    retrieved_bytes = await temp_storage.get_file_bytes(rel_path)
    assert retrieved_bytes == dummy_pdf_content


@pytest.mark.asyncio
async def test_delete_file_idempotency(temp_storage: LocalStorageService):
    """Test file deletion and idempotent behavior on non-existent file."""
    workspace_id = uuid.uuid4()
    document_id = uuid.uuid4()
    data = b"temp data"

    rel_path, _, _ = await temp_storage.save_file(
        workspace_id=workspace_id,
        document_id=document_id,
        file_data=data,
    )
    assert temp_storage.file_exists(rel_path) is True

    # Delete existing
    deleted = await temp_storage.delete_file(rel_path)
    assert deleted is True
    assert temp_storage.file_exists(rel_path) is False

    # Delete again (must be idempotent True)
    deleted_again = await temp_storage.delete_file(rel_path)
    assert deleted_again is True


@pytest.mark.asyncio
async def test_path_traversal_prevention(temp_storage: LocalStorageService):
    """Test that path traversal attempts raise StorageSecurityError."""
    with pytest.raises(StorageSecurityError):
        await temp_storage.get_file_bytes("../../etc/passwd")

    with pytest.raises(StorageSecurityError):
        await temp_storage.get_file_bytes("..\\..\\windows\\system32\\cmd.exe")


def test_missing_supabase_storage_credentials_in_production_fails():
    """Verify application fails closed if STORAGE_BACKEND is supabase but credentials are missing."""
    from backend.app.core.config import Settings

    with pytest.raises(ValueError, match="SUPABASE_SERVICE_ROLE_KEY must be configured"):
        Settings(
            ENVIRONMENT="production",
            DATABASE_URL="postgresql://user:pass@host/db",
            STORAGE_BACKEND="supabase",
            SUPABASE_SERVICE_ROLE_KEY="",
            _env_file=None,
        )


def test_explicit_local_storage_mode_permitted():
    """Verify STORAGE_BACKEND='local' is explicitly permitted without Supabase keys."""
    from backend.app.core.config import Settings

    dev_settings = Settings(
        ENVIRONMENT="development",
        DATABASE_URL="postgresql://user:pass@host/db",
        STORAGE_BACKEND="local",
        SUPABASE_SERVICE_ROLE_KEY="",
        _env_file=None,
    )
    assert dev_settings.STORAGE_BACKEND == "local"



@pytest.mark.asyncio
async def test_supabase_storage_path_sanitization():
    """Verify SupabaseStorageService enforces path security and blocks traversal."""
    from backend.app.storage.supabase import SupabaseStorageService

    service = SupabaseStorageService(
        supabase_url="https://test.supabase.co",
        service_role_key="test-key",
        bucket="prooflens-documents",
    )

    # Valid paths
    assert service._sanitize_path("workspaces/123/raw/doc.pdf") == "workspaces/123/raw/doc.pdf"
    assert service._sanitize_path("/workspaces/123/raw/doc.pdf/") == "workspaces/123/raw/doc.pdf"

    # Traversal attempts must fail closed
    with pytest.raises(StorageSecurityError):
        service._sanitize_path("../../etc/passwd")

    with pytest.raises(StorageSecurityError):
        service._sanitize_path("workspaces/../secret.key")

    with pytest.raises(StorageSecurityError):
        service._sanitize_path("workspaces/123/raw/doc;rm -rf /")


@pytest.mark.asyncio
async def test_supabase_storage_live_if_configured():
    """Verify live Supabase Storage bucket CRUD if valid credentials are configured."""
    from backend.app.core.config import settings
    from backend.app.storage.supabase import SupabaseStorageService

    if not settings.SUPABASE_SERVICE_ROLE_KEY or settings.STORAGE_BACKEND != "supabase":
        pytest.skip("Supabase service_role key not configured; skipping live storage test.")

    service = SupabaseStorageService(
        supabase_url=settings.SUPABASE_URL,
        service_role_key=settings.SUPABASE_SERVICE_ROLE_KEY,
        bucket=settings.SUPABASE_STORAGE_BUCKET,
    )

    workspace_id = uuid.uuid4()
    document_id = uuid.uuid4()
    content = b"%PDF-1.4 Live Supabase Cloud Storage Test"

    # 1. Save file
    path, sha256_hash, size = await service.save_file(
        workspace_id=workspace_id,
        document_id=document_id,
        file_data=content,
        extension=".pdf",
    )
    assert path == f"workspaces/{workspace_id}/raw/{document_id}.pdf"
    assert sha256_hash == hashlib.sha256(content).hexdigest()
    assert size == len(content)

    # 2. Get file
    fetched = await service.get_file_bytes(path)
    assert fetched == content

    # 3. Create signed URL
    signed_url = await service.create_signed_url(path, expires_in_seconds=600)
    assert "https://" in signed_url
    assert "prooflens-documents" in signed_url

    # 4. Delete file (idempotent)
    deleted = await service.delete_file(path)
    assert deleted is True

    # 5. Verify deletion (with retry for cloud storage propagation delay)
    deleted_verified = False
    import asyncio
    for _ in range(8):
        try:
            await service.get_file_bytes(path)
            await asyncio.sleep(0.3)
        except FileNotFoundError:
            deleted_verified = True
            break

    assert deleted_verified is True

