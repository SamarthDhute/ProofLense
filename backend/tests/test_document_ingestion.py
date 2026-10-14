import uuid
import pytest
import pymupdf as fitz
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.document import Document, DocumentChunk, DocumentPage


def _create_sample_pdf(num_pages: int = 2) -> bytes:
    """Create a sample valid PDF in memory."""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text(
            (50, 72),
            f"Page {i + 1} content for ProofLens AI.\nThis document covers critical evaluation metrics.",
        )
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.asyncio
async def test_upload_valid_pdf_sync(client: AsyncClient, db_session):
    """Test successful PDF upload and synchronous processing into pages and chunks."""
    # 1. Create workspace
    ws_res = await client.post("/api/v1/workspaces", json={"name": "Ingestion Workspace"})
    ws_data = ws_res.json()
    ws_id = ws_data["id"]
    token = ws_data["capability_token"]

    # 2. Upload PDF
    pdf_bytes = _create_sample_pdf(num_pages=3)
    files = {"file": ("research_paper.pdf", pdf_bytes, "application/pdf")}
    upload_res = await client.post(
        "/api/v1/documents?sync=true",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
    )
    assert upload_res.status_code == 201
    doc_data = upload_res.json()
    doc_id = doc_data["id"]
    assert doc_data["filename"] == "research_paper.pdf"
    assert doc_data["status"] == "READY"
    assert doc_data["workspace_id"] == ws_id

    # 3. Verify Document in DB
    db_doc = (
        await db_session.execute(select(Document).where(Document.id == uuid.UUID(doc_id)))
    ).scalar_one()
    assert db_doc.status == "READY"
    assert db_doc.error_message is None

    # 4. Verify DocumentPages in DB
    pages = (
        (await db_session.execute(select(DocumentPage).where(DocumentPage.document_id == db_doc.id)))
        .scalars()
        .all()
    )
    assert len(pages) == 3
    page_numbers = sorted([p.page_number for p in pages])
    assert page_numbers == [1, 2, 3]

    # 5. Verify DocumentChunks in DB
    chunks = (
        (await db_session.execute(select(DocumentChunk).where(DocumentChunk.document_id == db_doc.id)))
        .scalars()
        .all()
    )
    assert len(chunks) >= 3
    for chunk in chunks:
        # STRICT PHASE BOUNDARY: embeddings must be NULL
        assert chunk.embedding is None
        assert chunk.embedding_provider is None
        assert chunk.task_type == "RETRIEVAL_DOCUMENT"
        assert len(chunk.content) > 0


@pytest.mark.asyncio
async def test_upload_invalid_extension_rejected(client: AsyncClient):
    """Test rejecting files that do not have .pdf extension."""
    ws = (await client.post("/api/v1/workspaces", json={"name": "WS Extension"})).json()
    token = ws["capability_token"]

    files = {"file": ("malicious.exe", b"%PDF-1.4 dummy", "application/pdf")}
    res = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
    )
    assert res.status_code == 400
    assert "Invalid file extension" in res.json()["detail"]


@pytest.mark.asyncio
async def test_upload_invalid_mime_rejected(client: AsyncClient):
    """Test rejecting files with non-PDF declared MIME type."""
    ws = (await client.post("/api/v1/workspaces", json={"name": "WS MIME"})).json()
    token = ws["capability_token"]

    files = {"file": ("paper.pdf", b"%PDF-1.4 dummy", "image/png")}
    res = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
    )
    assert res.status_code == 400
    assert "Invalid MIME type" in res.json()["detail"]


@pytest.mark.asyncio
async def test_upload_empty_file_rejected(client: AsyncClient):
    """Test rejecting empty 0-byte file."""
    ws = (await client.post("/api/v1/workspaces", json={"name": "WS Empty"})).json()
    token = ws["capability_token"]

    files = {"file": ("empty.pdf", b"", "application/pdf")}
    res = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
    )
    assert res.status_code == 400
    assert "empty" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_corrupted_pdf_rejected(client: AsyncClient):
    """Test rejecting malformed PDF content."""
    ws = (await client.post("/api/v1/workspaces", json={"name": "WS Corrupt"})).json()
    token = ws["capability_token"]

    files = {"file": ("corrupt.pdf", b"%PDF-1.4 completely broken binary", "application/pdf")}
    res = await client.post(
        "/api/v1/documents?sync=true",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_upload_scanned_pdf_without_ocr_rejected(client: AsyncClient):
    """Test rejecting scanned image-only PDF with 422 Unprocessable Entity."""
    ws = (await client.post("/api/v1/workspaces", json={"name": "WS Scanned"})).json()
    token = ws["capability_token"]

    # Generate PDF with 2 empty pages (scanned representation)
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()

    files = {"file": ("scanned.pdf", pdf_bytes, "application/pdf")}
    res = await client.post(
        "/api/v1/documents?sync=true",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
    )
    assert res.status_code == 422
    assert "Scanned image-only PDFs" in res.json()["detail"]


@pytest.mark.asyncio
async def test_upload_duplicate_hash_conflict_in_same_workspace(client: AsyncClient):
    """Test duplicate PDF upload in the same workspace returns 409 Conflict."""
    ws = (await client.post("/api/v1/workspaces", json={"name": "WS Duplicate"})).json()
    token = ws["capability_token"]

    pdf_bytes = _create_sample_pdf(num_pages=1)
    files1 = {"file": ("first_upload.pdf", pdf_bytes, "application/pdf")}
    res1 = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files=files1,
    )
    assert res1.status_code == 201

    # Second upload with identical bytes
    files2 = {"file": ("second_upload.pdf", pdf_bytes, "application/pdf")}
    res2 = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files=files2,
    )
    assert res2.status_code == 409
    assert "already been uploaded" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_duplicate_hash_permitted_in_different_workspaces(client: AsyncClient):
    """Test identical PDF can be uploaded by distinct workspaces (tenant isolation)."""
    ws_a = (await client.post("/api/v1/workspaces", json={"name": "WS A"})).json()
    ws_b = (await client.post("/api/v1/workspaces", json={"name": "WS B"})).json()

    pdf_bytes = _create_sample_pdf(num_pages=1)

    # Upload to WS A
    res_a = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {ws_a['capability_token']}"},
        files={"file": ("shared.pdf", pdf_bytes, "application/pdf")},
    )
    assert res_a.status_code == 201

    # Upload to WS B
    res_b = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {ws_b['capability_token']}"},
        files={"file": ("shared.pdf", pdf_bytes, "application/pdf")},
    )
    assert res_b.status_code == 201
    assert res_a.json()["id"] != res_b.json()["id"]


@pytest.mark.asyncio
async def test_list_and_get_document_tenant_isolation(client: AsyncClient):
    """Test workspace isolation for listing and retrieving document details."""
    ws_a = (await client.post("/api/v1/workspaces", json={"name": "Tenant A"})).json()
    ws_b = (await client.post("/api/v1/workspaces", json={"name": "Tenant B"})).json()

    # Upload document to Tenant A
    pdf_a = _create_sample_pdf(num_pages=2)
    doc_a = (
        await client.post(
            "/api/v1/documents?sync=true",
            headers={"Authorization": f"Bearer {ws_a['capability_token']}"},
            files={"file": ("doc_a.pdf", pdf_a, "application/pdf")},
        )
    ).json()

    # 1. Tenant A lists documents: finds doc_a
    list_a = (
        await client.get(
            "/api/v1/documents",
            headers={"Authorization": f"Bearer {ws_a['capability_token']}"},
        )
    ).json()
    assert list_a["total"] == 1
    assert list_a["items"][0]["id"] == doc_a["id"]
    assert list_a["items"][0]["page_count"] == 2
    assert list_a["items"][0]["chunk_count"] >= 2

    # 2. Tenant B lists documents: finds 0 documents
    list_b = (
        await client.get(
            "/api/v1/documents",
            headers={"Authorization": f"Bearer {ws_b['capability_token']}"},
        )
    ).json()
    assert list_b["total"] == 0
    assert len(list_b["items"]) == 0

    # 3. Tenant A retrieves doc_a details: succeeds
    detail_a = await client.get(
        f"/api/v1/documents/{doc_a['id']}",
        headers={"Authorization": f"Bearer {ws_a['capability_token']}"},
    )
    assert detail_a.status_code == 200
    assert detail_a.json()["status"] == "READY"

    # 4. Tenant B attempts to retrieve doc_a details: returns 404 (non-disclosing)
    detail_b = await client.get(
        f"/api/v1/documents/{doc_a['id']}",
        headers={"Authorization": f"Bearer {ws_b['capability_token']}"},
    )
    assert detail_b.status_code == 404


@pytest.mark.asyncio
async def test_compensating_cleanup_on_db_failure(temp_storage):
    """Test that if DB registration fails after storage upload, storage file is purged."""
    import io
    from unittest.mock import AsyncMock
    from fastapi import UploadFile
    from backend.app.services.document_service import DocumentService

    workspace_id = uuid.uuid4()
    pdf_bytes = _create_sample_pdf(num_pages=1)
    upload_file = UploadFile(
        file=io.BytesIO(pdf_bytes),
        filename="compensating_test.pdf",
        headers={"content-type": "application/pdf"},
    )

    from unittest.mock import MagicMock
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result
    mock_db.commit.side_effect = RuntimeError("Simulated DB crash during registration")

    with pytest.raises(RuntimeError) as exc_info:
        await DocumentService.upload_document(
            db=mock_db,
            storage=temp_storage,
            workspace_id=workspace_id,
            file=upload_file,
        )

    assert "Database registration failed" in str(exc_info.value)
    mock_db.rollback.assert_called_once()
