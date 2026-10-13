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
