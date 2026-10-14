import uuid
from datetime import datetime, timedelta, timezone
import pytest
import pymupdf as fitz
from httpx import AsyncClient
from sqlalchemy import select, update

from backend.app.models.document import Document


def _create_sample_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), "Text for recovery tests in ProofLens AI.")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.asyncio
async def test_retry_failed_document(client: AsyncClient, db_session):
    """Test manual retry of a FAILED document succeeds and transitions to READY."""
    ws = (await client.post("/api/v1/workspaces", json={"name": "Retry WS"})).json()
    token = ws["capability_token"]

    pdf_bytes = _create_sample_pdf()
    res = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("retry_doc.pdf", pdf_bytes, "application/pdf")},
    )
    doc_id = res.json()["id"]
    doc_uuid = uuid.UUID(doc_id)

    # Force document into FAILED status to simulate previous processing failure
    await db_session.execute(
        update(Document)
        .where(Document.id == doc_uuid)
        .values(status="FAILED", error_message="Simulated temporary crash")
    )
    await db_session.commit()

    # Call retry endpoint
    retry_res = await client.post(
        f"/api/v1/documents/{doc_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert retry_res.status_code == 200
    retry_data = retry_res.json()
    assert retry_data["status"] == "READY"
    assert retry_data["error_message"] is None
    assert retry_data["page_count"] == 1


@pytest.mark.asyncio
async def test_retry_ready_document_rejected(client: AsyncClient):
    """Test that retrying an already READY document returns 400 Bad Request."""
    ws = (await client.post("/api/v1/workspaces", json={"name": "Retry Ready WS"})).json()
    token = ws["capability_token"]

    pdf_bytes = _create_sample_pdf()
    res = await client.post(
        "/api/v1/documents?sync=true",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("ready_doc.pdf", pdf_bytes, "application/pdf")},
    )
    doc_id = res.json()["id"]

    retry_res = await client.post(
        f"/api/v1/documents/{doc_id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert retry_res.status_code == 400
    assert "Cannot retry document in status 'READY'" in retry_res.json()["detail"]


@pytest.mark.asyncio
async def test_stale_job_recovery(client: AsyncClient, db_session):
    """Test recovery picks up stale interrupted documents and reprocesses them."""
    ws = (await client.post("/api/v1/workspaces", json={"name": "Stale WS"})).json()
    token = ws["capability_token"]

    pdf_bytes = _create_sample_pdf()
    res = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("stale_doc.pdf", pdf_bytes, "application/pdf")},
    )
    doc_id = res.json()["id"]
    doc_uuid = uuid.UUID(doc_id)

    # Simulate an interrupted job by setting status to EXTRACTING and old heartbeat
    old_time = datetime.now(timezone.utc) - timedelta(seconds=1200)
    await db_session.execute(
        update(Document)
        .where(Document.id == doc_uuid)
        .values(
            status="EXTRACTING",
            processing_heartbeat=old_time,
            updated_at=old_time,
        )
    )
    await db_session.commit()

    # Trigger recovery with grace_period_seconds = 300
    rec_res = await client.post(
        "/api/v1/documents/recovery/stale-jobs?grace_period_seconds=300",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rec_res.status_code == 200
    rec_data = rec_res.json()
    assert rec_data["recovered_count"] >= 1

    # Verify the document is now READY
    detail = await client.get(
        f"/api/v1/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail.json()["status"] == "READY"


@pytest.mark.asyncio
async def test_stale_job_recovery_bounded_retries(client: AsyncClient, db_session):
    """Test recovery permanently marks document as FAILED when max retries exceeded."""
    ws = (await client.post("/api/v1/workspaces", json={"name": "Max Retries WS"})).json()
    token = ws["capability_token"]

    pdf_bytes = _create_sample_pdf()
    res = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("crash_loop.pdf", pdf_bytes, "application/pdf")},
    )
    doc_id = res.json()["id"]
    doc_uuid = uuid.UUID(doc_id)

    old_time = datetime.now(timezone.utc) - timedelta(seconds=1200)
    # Set deletion_attempts to 3 (equal to MAX_PROCESSING_RETRIES)
    await db_session.execute(
        update(Document)
        .where(Document.id == doc_uuid)
        .values(
            status="EXTRACTING",
            processing_heartbeat=old_time,
            updated_at=old_time,
            deletion_attempts=3,
        )
    )
    await db_session.commit()

    # Trigger recovery
    rec_res = await client.post(
        "/api/v1/documents/recovery/stale-jobs?grace_period_seconds=300",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rec_res.status_code == 200
    rec_data = rec_res.json()
    assert rec_data["permanently_failed_count"] >= 1

    # Verify document is permanently FAILED
    detail = await client.get(
        f"/api/v1/documents/{doc_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert detail.json()["status"] == "FAILED"
    assert "maximum recovery attempts" in detail.json()["error_message"]


@pytest.mark.asyncio
async def test_recovery_endpoint_workspace_isolation(client: AsyncClient, db_session):
    """Verify that recovery triggered by Workspace A does NOT touch or leak Workspace B's documents."""
    ws_a = (await client.post("/api/v1/workspaces", json={"name": "Recovery Tenant A"})).json()
    token_a = ws_a["capability_token"]

    ws_b = (await client.post("/api/v1/workspaces", json={"name": "Recovery Tenant B"})).json()
    token_b = ws_b["capability_token"]

    pdf_bytes = _create_sample_pdf()
    res_b = await client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {token_b}"},
        files={"file": ("tenant_b_stale.pdf", pdf_bytes, "application/pdf")},
    )
    doc_b_id = res_b.json()["id"]
    doc_b_uuid = uuid.UUID(doc_b_id)

    # Set Workspace B's document to stale EXTRACTING
    old_time = datetime.now(timezone.utc) - timedelta(seconds=1200)
    await db_session.execute(
        update(Document)
        .where(Document.id == doc_b_uuid)
        .values(
            status="EXTRACTING",
            processing_heartbeat=old_time,
            updated_at=old_time,
        )
    )
    await db_session.commit()

    # Workspace A triggers recovery
    rec_res_a = await client.post(
        "/api/v1/documents/recovery/stale-jobs?grace_period_seconds=300",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert rec_res_a.status_code == 200
    rec_data_a = rec_res_a.json()
    assert rec_data_a["recovered_count"] == 0
    assert len(rec_data_a["results"]) == 0

    # Verify Workspace B's document is still in EXTRACTING
    b_detail = await client.get(
        f"/api/v1/documents/{doc_b_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert b_detail.json()["status"] == "EXTRACTING"

    # Workspace B triggers recovery for its own workspace
    rec_res_b = await client.post(
        "/api/v1/documents/recovery/stale-jobs?grace_period_seconds=300",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert rec_res_b.status_code == 200
    assert rec_res_b.json()["recovered_count"] == 1

