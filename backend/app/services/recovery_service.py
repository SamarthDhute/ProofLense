from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.models.document import Document
from backend.app.services.document_service import DocumentService
from backend.app.storage.base import StorageService


@dataclass
class StaleDocumentRecoveryResult:
    document_id: UUID
    action: str  # "REPROCESSED", "MARKED_FAILED", "SKIPPED"
    status: str
    error: Optional[str] = None


@dataclass
class RecoveryBatchSummary:
    recovered_count: int
    permanently_failed_count: int
    results: List[StaleDocumentRecoveryResult]


class RecoveryService:
    """Detects and safely recovers documents interrupted by process crashes or timeouts."""

    @classmethod
    async def recover_stale_documents(
        cls,
        db: AsyncSession,
        storage: StorageService,
        workspace_id: Optional[UUID] = None,
        grace_period_seconds: Optional[int] = None,
        max_retries: Optional[int] = None,
        worker_id: str = "recovery-worker",
    ) -> RecoveryBatchSummary:
        """Scan and recover documents stuck in in-flight states (PENDING, EXTRACTING, CHUNKING).
        
        Guarantees:
        - Respects grace period (does not touch actively running jobs).
        - Idempotent execution.
        - Skips documents in DELETING status.
        - Bounded retries: permanently marks as FAILED if retry limit reached.
        - Strictly isolated by workspace_id when invoked from an authenticated tenant context.
        """
        grace_seconds = grace_period_seconds or settings.INGESTION_LEASE_TIMEOUT_SECONDS
        retry_limit = max_retries or settings.MAX_PROCESSING_RETRIES
        cutoff_time = datetime.now(timezone.utc) - timedelta(seconds=grace_seconds)

        # Query candidate IDs and attributes upfront to prevent lazy-loading after commits
        query_conditions = [
            Document.status.in_(["PENDING", "EXTRACTING", "CHUNKING"]),
            Document.updated_at < cutoff_time,
        ]
        if workspace_id is not None:
            query_conditions.append(Document.workspace_id == workspace_id)

        stmt = (
            select(
                Document.id,
                Document.workspace_id,
                Document.status,
                Document.processing_heartbeat,
                Document.deletion_attempts,
            )
            .where(*query_conditions)
            .order_by(Document.updated_at.asc())
        )

        candidates = (await db.execute(stmt)).all()

        recovered_count = 0
        permanently_failed_count = 0
        results: List[StaleDocumentRecoveryResult] = []

        for doc_id, ws_id, doc_status, heartbeat, attempts in candidates:
            # Additional check on processing_heartbeat if present
            if heartbeat and heartbeat > cutoff_time:
                # Active worker heartbeat is still fresh
                results.append(
                    StaleDocumentRecoveryResult(
                        document_id=doc_id,
                        action="SKIPPED",
                        status=doc_status,
                        error="Active worker heartbeat is fresh.",
                    )
                )
                continue

            # Fetch fresh document instance
            doc_res = await db.execute(select(Document).where(Document.id == doc_id))
            doc = doc_res.scalar_one_or_none()
            if not doc or doc.status == "DELETING":
                continue

            # Bounded retry policy
            current_attempts = attempts or 0
            if current_attempts >= retry_limit:
                doc.status = "FAILED"
                doc.error_message = f"Processing timed out or crashed; maximum recovery attempts ({retry_limit}) exceeded."
                doc.processing_heartbeat = datetime.now(timezone.utc)
                await db.commit()
                permanently_failed_count += 1
                results.append(
                    StaleDocumentRecoveryResult(
                        document_id=doc.id,
                        action="MARKED_FAILED",
                        status="FAILED",
                        error=doc.error_message,
                    )
                )
                continue

            # Increment attempt counter
            doc.deletion_attempts = current_attempts + 1
            doc.status = "PENDING"
            doc.worker_id = worker_id
            doc.processing_heartbeat = datetime.now(timezone.utc)
            await db.commit()

            try:
                processed_doc = await DocumentService.process_document(
                    db=db,
                    storage=storage,
                    document_id=doc.id,
                    workspace_id=doc.workspace_id,
                    worker_id=worker_id,
                )
                recovered_count += 1
                results.append(
                    StaleDocumentRecoveryResult(
                        document_id=doc.id,
                        action="REPROCESSED",
                        status=processed_doc.status,
                    )
                )
            except Exception as exc:
                permanently_failed_count += 1
                results.append(
                    StaleDocumentRecoveryResult(
                        document_id=doc.id,
                        action="MARKED_FAILED",
                        status="FAILED",
                        error=f"Recovery re-processing error: {str(exc)}",
                    )
                )

        return RecoveryBatchSummary(
            recovered_count=recovered_count,
            permanently_failed_count=permanently_failed_count,
            results=results,
        )
