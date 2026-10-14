import uuid
import pytest
import pymupdf as fitz
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.document import Document, DocumentChunk, DocumentPage


def _create_sample_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), "Sample text for durable deletion tests.")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.asyncio
async def test_durable_document_deletion(client: AsyncClient, temp_storage, db_session):
    """Test durable document deletion removes storage object, document, pages, and chunks."""
    # 1. Create workspace
    ws = (await client.post("/api/v1/workspaces", json={"name": "Deletion WS"})).json()
    token = ws["capability_token"]

    # 2. Upload document synchronously
    pdf_bytes = _create_sample_pdf()
    files = {"file": ("delete_me.pdf", pdf_bytes, "application/pdf")}
    res = await client.post(
        "/api/v1/documents?sync=true",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
    )
    assert res.status_code == 201
    doc_id = res.json()["id"]

    # Verify storage object exists
    storage_path = f"workspaces/{ws['id']}/raw/{doc_id}.pdf"
    assert temp_storage.file_exists(storage_path) is True

    # Verify DB records exist
    doc_uuid = uuid.UUID(doc_id)
    pages = (
        (await db_session.execute(select(DocumentPage).where(DocumentPage.document_id == doc_uuid)))
        .scalars()
        .all()
    )
    chunks = (
        (await db_session.execute(select(DocumentChunk).where(DocumentChunk.document_id == doc_uuid)))
        .scalars()
        .all()
    )
    assert len(pages) > 0
    assert len(chunks) > 0

    # 3. Issue DELETE request
    del_res = await client.delete(
        f"/api/v1/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True

    # 4. Verify storage object is permanently gone
    assert temp_storage.file_exists(storage_path) is False

    # 5. Verify database document record is permanently deleted
    db_doc = (
        await db_session.execute(select(Document).where(Document.id == doc_uuid))
    ).scalar_one_or_none()
    assert db_doc is None

    # 6. Verify pages and chunks are cascaded away
    del_pages = (
        (await db_session.execute(select(DocumentPage).where(DocumentPage.document_id == doc_uuid)))
        .scalars()
        .all()
    )
    del_chunks = (
        (await db_session.execute(select(DocumentChunk).where(DocumentChunk.document_id == doc_uuid)))
        .scalars()
        .all()
    )
    assert len(del_pages) == 0
    assert len(del_chunks) == 0


@pytest.mark.asyncio
async def test_idempotent_repeated_deletion(client: AsyncClient):
    """Test calling DELETE on an already deleted document succeeds idempotently."""
    ws = (await client.post("/api/v1/workspaces", json={"name": "Idempotent WS"})).json()
    token = ws["capability_token"]

    pdf_bytes = _create_sample_pdf()
    res = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
    )
    doc_id = res.json()["id"]

    # First delete
    del1 = await client.delete(
        f"/api/v1/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del1.status_code == 200
    assert del1.json()["deleted"] is True

    # Second delete (idempotent)
    del2 = await client.delete(
        f"/api/v1/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del2.status_code == 200
    assert del2.json()["deleted"] is True


@pytest.mark.asyncio
async def test_cross_workspace_deletion_blocked(client: AsyncClient, temp_storage, db_session):
    """Test Workspace B cannot delete Workspace A's document."""
    ws_a = (await client.post("/api/v1/workspaces", json={"name": "WS A Isolation"})).json()
    ws_b = (await client.post("/api/v1/workspaces", json={"name": "WS B Isolation"})).json()

    pdf_bytes = _create_sample_pdf()
    res = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {ws_a['capability_token']}"},
        files={"file": ("target.pdf", pdf_bytes, "application/pdf")},
    )
    doc_id = res.json()["id"]
    storage_path = f"workspaces/{ws_a['id']}/raw/{doc_id}.pdf"

    # Workspace B attempts to delete Workspace A's document
    del_res = await client.delete(
        f"/api/v1/documents/{doc_id}",
        headers={"Authorization": f"Bearer {ws_b['capability_token']}"},
    )
    # Returns 200 with deleted=True idempotently because document doesn't exist in WS B,
    # BUT must NOT delete the actual document in WS A!
    assert del_res.status_code == 200

    # Verify WS A's storage file and DB document are completely intact!
    assert temp_storage.file_exists(storage_path) is True
    doc_uuid = uuid.UUID(doc_id)
    db_doc = (
        await db_session.execute(select(Document).where(Document.id == doc_uuid))
    ).scalar_one_or_none()
    assert db_doc is not None
    assert db_doc.workspace_id == uuid.UUID(ws_a["id"])


@pytest.mark.asyncio
async def test_document_deletion_marks_historical_citations(client: AsyncClient, db_session):
    """Verify deleting a document marks historical citations referencing it with document_deleted: true."""
    from backend.app.models.conversation import Conversation, Message

    ws = (await client.post("/api/v1/workspaces", json={"name": "Citation Delete WS"})).json()
    token = ws["capability_token"]
    ws_uuid = uuid.UUID(ws["id"])

    pdf_bytes = _create_sample_pdf()
    res = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("cited_doc.pdf", pdf_bytes, "application/pdf")},
    )
    doc_id = res.json()["id"]

    # Create a conversation and message referencing this document in citations
    conv = Conversation(id=uuid.uuid4(), workspace_id=ws_uuid)
    db_session.add(conv)
    await db_session.flush()

    msg = Message(
        id=uuid.uuid4(),
        conversation_id=conv.id,
        role="assistant",
        content="Grounded statement based on cited document.",
        citations=[{
            "document_id": str(doc_id),
            "page_number": 1,
            "excerpt": "Sample text for durable deletion tests.",
        }],
    )
    db_session.add(msg)
    await db_session.commit()

    # Delete the document
    del_res = await client.delete(
        f"/api/v1/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_res.status_code == 200

    # Query message citations
    refetched_msg = (
        await db_session.execute(select(Message).where(Message.id == msg.id))
    ).scalar_one()

    assert refetched_msg.citations[0]["document_id"] == str(doc_id)
    assert refetched_msg.citations[0]["document_deleted"] is True
    assert refetched_msg.citations[0]["excerpt"] == "Sample text for durable deletion tests."

