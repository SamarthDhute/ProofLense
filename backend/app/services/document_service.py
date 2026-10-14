import hashlib
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.config import settings
from backend.app.models.document import Document, DocumentChunk, DocumentPage
from backend.app.services.pdf_extractor import (
    PdfCorruptedError,
    PdfExtractorService,
    PdfPasswordProtectedError,
    PdfScannedError,
    PdfValidationError,
)
from backend.app.services.text_chunker import TextChunkerService
from backend.app.storage.base import StorageService


class DocumentServiceException(Exception):
    """Base exception for document service errors."""
    pass


class DocumentValidationError(DocumentServiceException):
    """Raised when uploaded file fails validation."""
    pass


class DocumentDuplicateError(DocumentServiceException):
    """Raised when an identical document hash already exists in the workspace."""
    pass


class DocumentNotFoundError(DocumentServiceException):
    """Raised when a requested document does not exist in the tenant workspace."""
    pass


class DocumentStateError(DocumentServiceException):
    """Raised when an invalid state transition is attempted."""
    pass


class DocumentService:
    """Service handling document upload, validation, extraction, chunking, and durable deletion."""

    @staticmethod
    def sanitize_filename(filename: Optional[str]) -> str:
        """Sanitize uploaded filename to prevent directory traversal and illegal characters."""
        if not filename or not filename.strip():
            return "unnamed.pdf"

        # Extract only base filename without paths
        raw_name = Path(filename).name
        # Remove path traversal tokens and illegal characters
        clean_name = re.sub(r"[^\w\s\.-]", "_", raw_name).strip()
        if not clean_name:
            clean_name = "document.pdf"
        if not clean_name.lower().endswith(".pdf"):
            clean_name += ".pdf"
        return clean_name[:255]

    @classmethod
    async def validate_and_read_upload(cls, file: UploadFile) -> Tuple[bytes, str, str]:
        """Validate upload file extension, content-type, size cap, and magic bytes.
        
        Returns:
            Tuple of (file_bytes, sanitized_filename, sha256_hash)
        """
        raw_filename = file.filename or ""
        sanitized_filename = cls.sanitize_filename(raw_filename)

        # 1. Extension check
        if not raw_filename.lower().endswith(".pdf"):
            raise DocumentValidationError("Invalid file extension. Only .pdf files are accepted.")

        # 2. Declared content-type check
        allowed_mimes = {
            "application/pdf",
            "application/x-pdf",
            "application/acrobat",
            "applications/vnd.pdf",
            "text/pdf",
            "text/x-pdf",
            "application/octet-stream",  # Permitted only if file is confirmed PDF
        }
        declared_mime = (file.content_type or "").lower().split(";")[0].strip()
        if declared_mime and declared_mime not in allowed_mimes:
            raise DocumentValidationError(f"Invalid MIME type '{declared_mime}'. Expected 'application/pdf'.")

        # 3. Streamed byte-size enforcement
        max_bytes = settings.MAX_DOCUMENT_SIZE_MB * 1024 * 1024
        content_chunks: List[bytes] = []
        total_read = 0
        sha256_hasher = hashlib.sha256()

        chunk_size = 64 * 1024  # 64 KB chunks
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            total_read += len(chunk)
            if total_read > max_bytes:
                raise DocumentValidationError(
                    f"File size exceeds maximum permitted limit of {settings.MAX_DOCUMENT_SIZE_MB}MB."
                )
            content_chunks.append(chunk)
            sha256_hasher.update(chunk)

        file_bytes = b"".join(content_chunks)
        if len(file_bytes) == 0:
            raise DocumentValidationError("Uploaded file is empty (0 bytes).")

        # 4. PDF magic bytes and parseability pre-validation
        try:
            PdfExtractorService.validate_pdf_bytes(file_bytes)
            import pymupdf as fitz
            try:
                doc = fitz.open(stream=file_bytes, filetype="pdf")
            except Exception as exc:
                raise DocumentValidationError(f"Malformed or corrupted PDF structure: {str(exc)}") from exc

            try:
                if doc.is_encrypted or doc.needs_pass:
                    raise PdfPasswordProtectedError("Encrypted or password-protected PDFs are not supported.")
                if doc.page_count == 0:
                    raise DocumentValidationError("PDF document contains 0 pages.")

                # Reject scanned image-only PDFs without an embedded text layer
                total_text_chars = sum(len(doc.load_page(i).get_text("text").strip()) for i in range(doc.page_count))
                if total_text_chars == 0:
                    raise PdfScannedError(
                        "Document contains no extractable text across all pages. "
                        "Scanned image-only PDFs without an embedded text layer are not supported."
                    )
            finally:
                doc.close()
        except (DocumentValidationError, PdfPasswordProtectedError, PdfScannedError):
            raise
        except PdfValidationError as exc:
            raise DocumentValidationError(str(exc)) from exc
        except Exception as exc:
            raise DocumentValidationError(f"Malformed or unreadable PDF: {str(exc)}") from exc

        file_hash = sha256_hasher.hexdigest()
        return file_bytes, sanitized_filename, file_hash

    @classmethod
    async def upload_document(
        cls,
        db: AsyncSession,
        storage: StorageService,
        workspace_id: UUID,
        file: UploadFile,
    ) -> Document:
        """Upload, validate, store PDF in workspace-scoped storage, and register PENDING record."""
        # 1. Validate and read bytes
        file_bytes, filename, file_hash = await cls.validate_and_read_upload(file)

        # 2. Check for duplicate content within this workspace
        existing = await db.execute(
            select(Document).where(
                Document.workspace_id == workspace_id,
                Document.file_hash == file_hash,
            )
        )
        duplicate_doc = existing.scalar_one_or_none()
        if duplicate_doc:
            if duplicate_doc.status != "DELETING":
                raise DocumentDuplicateError(
                    f"A document with identical content has already been uploaded: '{duplicate_doc.filename}' "
                    f"(ID: {duplicate_doc.id}, Status: {duplicate_doc.status})."
                )

        # 3. Generate server-side document ID
        document_id = uuid.uuid4()

        # 4. Save to storage under canonical workspace hierarchy
        # workspaces/{workspace_id}/raw/{document_id}.pdf
        try:
            storage_path, computed_hash, byte_size = await storage.save_file(
                workspace_id=workspace_id,
                document_id=document_id,
                file_data=file_bytes,
                extension=".pdf",
            )
        except Exception as exc:
            raise RuntimeError(f"Storage upload failed: {str(exc)}") from exc

        # 5. Database registration with compensating cleanup on failure
        doc = Document(
            id=document_id,
            workspace_id=workspace_id,
            filename=filename,
            file_hash=file_hash,
            mime_type="application/pdf",
            storage_path=storage_path,
            status="PENDING",
            processing_heartbeat=datetime.now(timezone.utc),
        )

        try:
            db.add(doc)
            await db.commit()
            await db.refresh(doc)
        except Exception as db_exc:
            await db.rollback()
            # COMPENSATING ACTION: delete uploaded storage object to prevent orphan file
            try:
                await storage.delete_file(storage_path)
            except Exception:
                pass
            raise RuntimeError(f"Database registration failed: {str(db_exc)}") from db_exc

        return doc

    @classmethod
    async def process_document(
        cls,
        db: AsyncSession,
        storage: StorageService,
        document_id: UUID,
        workspace_id: UUID,
        worker_id: str = "local-ingestion-worker",
        raise_on_error: bool = False,
    ) -> Document:
        """Execute full extraction and chunking pipeline for a registered document.
        
        State flow: PENDING -> EXTRACTING -> CHUNKING -> READY (or FAILED on error).
        Idempotent: Re-running cleans previous pages/chunks before repopulating.
        """
        # Fetch document
        result = await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.workspace_id == workspace_id,
            )
        )
        doc = result.scalar_one_or_none()
        if not doc:
            raise DocumentNotFoundError(f"Document {document_id} not found.")

        # Guard against processing documents being deleted
        if doc.status == "DELETING":
            return doc

        # Transition to EXTRACTING
        doc.status = "EXTRACTING"
        doc.worker_id = worker_id
        doc.processing_heartbeat = datetime.now(timezone.utc)
        doc.error_message = None
        await db.commit()
        await db.refresh(doc)

        try:
            # 1. Fetch source PDF from storage
            pdf_bytes = await storage.get_file_bytes(doc.storage_path)

            # 2. Extract pages deterministically via PyMuPDF
            extraction = PdfExtractorService.extract_text(pdf_bytes)

            # 3. Clean up any existing pages or chunks for idempotent re-processing
            await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
            await db.execute(delete(DocumentPage).where(DocumentPage.document_id == doc.id))
            await db.flush()

            # 4. Persist extracted pages to document_pages
            page_records: List[DocumentPage] = []
            for extracted_page in extraction.pages:
                page_rec = DocumentPage(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    workspace_id=doc.workspace_id,
                    page_number=extracted_page.page_number,
                    text_content=extracted_page.text,
                )
                db.add(page_rec)
                page_records.append(page_rec)

            await db.flush()

            # Re-check deletion state
            await db.refresh(doc)
            if doc.status == "DELETING":
                await db.rollback()
                return doc

            # Transition to CHUNKING
            doc.status = "CHUNKING"
            doc.processing_heartbeat = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(doc)

            # 5. Sentence-aware chunking
            chunker = TextChunkerService()
            running_chunk_index = 0

            for page_rec in page_records:
                if not page_rec.text_content:
                    continue

                chunks_data = chunker.chunk_page_text(
                    page_text=page_rec.text_content,
                    page_number=page_rec.page_number,
                    page_id=page_rec.id,
                    document_id=doc.id,
                    workspace_id=doc.workspace_id,
                    start_chunk_index=running_chunk_index,
                )

                for chunk_item in chunks_data:
                    chunk_rec = DocumentChunk(
                        id=uuid.uuid4(),
                        document_id=doc.id,
                        workspace_id=doc.workspace_id,
                        page_id=page_rec.id,
                        chunk_index=chunk_item.chunk_index,
                        content=chunk_item.content,
                        char_start=chunk_item.char_start,
                        char_end=chunk_item.char_end,
                        embedding=None,  # STRICT PHASE BOUNDARY: embeddings remain NULL
                        embedding_provider=None,
                        embedding_model=None,
                        embedding_version=None,
                        embedding_dimensions=None,
                        task_type="RETRIEVAL_DOCUMENT",
                    )
                    db.add(chunk_rec)

                running_chunk_index += len(chunks_data)

            await db.flush()

            # Final check before READY
            await db.refresh(doc)
            if doc.status == "DELETING":
                await db.rollback()
                return doc

            # Transition to READY
            doc.status = "READY"
            doc.error_message = None
            doc.processing_heartbeat = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(doc)
            return doc

        except Exception as exc:
            await db.rollback()
            # Mark as FAILED with sanitized error message
            error_text = str(exc)
            # Sanitize error to prevent internal path/secret leakage
            sanitized_error = re.sub(r"([A-Za-z]:)?(/|\\)[^\s]+", "[path]", error_text)
            if len(sanitized_error) > 500:
                sanitized_error = sanitized_error[:497] + "..."

            try:
                # Re-fetch and update status to FAILED
                refetch = await db.execute(
                    select(Document).where(Document.id == document_id)
                )
                fail_doc = refetch.scalar_one_or_none()
                if fail_doc and fail_doc.status != "DELETING":
                    fail_doc.status = "FAILED"
                    fail_doc.error_message = sanitized_error
                    fail_doc.processing_heartbeat = datetime.now(timezone.utc)
                    await db.commit()
                    if raise_on_error:
                        raise exc
                    return fail_doc
            except Exception as inner_exc:
                if raise_on_error:
                    raise exc
            raise

    @classmethod
    async def list_documents(
        cls,
        db: AsyncSession,
        workspace_id: UUID,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """List documents in authenticated workspace with page/chunk counts and stable pagination."""
        limit = min(max(1, limit), 100)
        skip = max(0, skip)

        # Count total
        count_query = select(func.count(Document.id)).where(
            Document.workspace_id == workspace_id,
            Document.status != "DELETING",
        )
        total_result = await db.execute(count_query)
        total = total_result.scalar_one() or 0

        # Subquery for page count
        page_count_subq = (
            select(func.count(DocumentPage.id))
            .where(DocumentPage.document_id == Document.id)
            .correlate(Document)
            .scalar_subquery()
        )

        # Subquery for chunk count
        chunk_count_subq = (
            select(func.count(DocumentChunk.id))
            .where(DocumentChunk.document_id == Document.id)
            .correlate(Document)
            .scalar_subquery()
        )

        # Query documents with aggregated counts
        stmt = (
            select(
                Document,
                page_count_subq.label("page_count"),
                chunk_count_subq.label("chunk_count"),
            )
            .where(
                Document.workspace_id == workspace_id,
                Document.status != "DELETING",
            )
            .order_by(Document.created_at.desc(), Document.id.asc())
            .offset(skip)
            .limit(limit)
        )

        rows = (await db.execute(stmt)).all()
        items = []
        for doc, page_cnt, chunk_cnt in rows:
            items.append({
                "id": doc.id,
                "workspace_id": doc.workspace_id,
                "filename": doc.filename,
                "file_hash": doc.file_hash,
                "mime_type": doc.mime_type,
                "status": doc.status,
                "page_count": page_cnt or 0,
                "chunk_count": chunk_cnt or 0,
                "error_message": doc.error_message,
                "created_at": doc.created_at,
                "updated_at": doc.updated_at,
            })

        return items, total

    @classmethod
    async def get_document(
        cls,
        db: AsyncSession,
        workspace_id: UUID,
        document_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve single document metadata and status, enforcing workspace tenant isolation."""
        page_count_subq = (
            select(func.count(DocumentPage.id))
            .where(DocumentPage.document_id == Document.id)
            .correlate(Document)
            .scalar_subquery()
        )
        chunk_count_subq = (
            select(func.count(DocumentChunk.id))
            .where(DocumentChunk.document_id == Document.id)
            .correlate(Document)
            .scalar_subquery()
        )

        stmt = select(
            Document,
            page_count_subq.label("page_count"),
            chunk_count_subq.label("chunk_count"),
        ).where(
            Document.id == document_id,
            Document.workspace_id == workspace_id,
        )

        row = (await db.execute(stmt)).first()
        if not row:
            return None

        doc, page_cnt, chunk_cnt = row
        return {
            "id": doc.id,
            "workspace_id": doc.workspace_id,
            "filename": doc.filename,
            "file_hash": doc.file_hash,
            "mime_type": doc.mime_type,
            "status": doc.status,
            "page_count": page_cnt or 0,
            "chunk_count": chunk_cnt or 0,
            "error_message": doc.error_message,
            "processing_heartbeat": doc.processing_heartbeat,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
        }

    @classmethod
    async def delete_document(
        cls,
        db: AsyncSession,
        storage: StorageService,
        workspace_id: UUID,
        document_id: UUID,
    ) -> bool:
        """Durable, idempotent document deletion covering Storage object and DB records.
        
        Guarantees:
        - Strict workspace authorization.
        - Idempotent: repeated calls on already deleted document return True.
        - Compensating error handling: Storage failure prevents premature DB purge.
        """
        # Fetch document with workspace filter
        res = await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.workspace_id == workspace_id,
            )
        )
        doc = res.scalar_one_or_none()
        if not doc:
            # Idempotent: document does not exist or was already deleted
            return True

        # 1. Mark status as DELETING to prevent concurrent processing jobs from running
        doc.status = "DELETING"
        doc.deletion_attempts += 1
        await db.commit()
        await db.refresh(doc)

        # 2. Delete storage file first
        storage_deleted = True
        try:
            storage_deleted = await storage.delete_file(doc.storage_path)
        except Exception as exc:
            doc.deletion_error = f"Storage deletion failed: {str(exc)}"
            await db.commit()
            raise RuntimeError(f"Storage deletion failed for {doc.storage_path}: {str(exc)}") from exc

        if not storage_deleted:
            doc.deletion_error = "Storage returned False during delete_file."
            await db.commit()
            raise RuntimeError(f"Storage deletion failed for {doc.storage_path}.")

        # 3. Flag historical conversation citations in messages belonging to this workspace
        try:
            from backend.app.models.conversation import Conversation, Message
            msg_stmt = (
                select(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(Conversation.workspace_id == workspace_id)
            )
            messages = (await db.execute(msg_stmt)).scalars().all()
            for msg in messages:
                if msg.citations and isinstance(msg.citations, list):
                    updated = False
                    new_citations = []
                    for cit in msg.citations:
                        if isinstance(cit, dict) and str(cit.get("document_id")) == str(document_id):
                            cit_copy = dict(cit)
                            cit_copy["document_deleted"] = True
                            new_citations.append(cit_copy)
                            updated = True
                        else:
                            new_citations.append(cit)
                    if updated:
                        msg.citations = new_citations
        except Exception:
            pass  # Best effort for historical citation marking

        # 4. Delete database record (cascades to pages and chunks via FKs)
        await db.delete(doc)
        await db.commit()
        return True

    @classmethod
    async def retry_document_processing(
        cls,
        db: AsyncSession,
        storage: StorageService,
        workspace_id: UUID,
        document_id: UUID,
    ) -> Document:
        """Reset a FAILED document to PENDING and trigger re-processing."""
        res = await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.workspace_id == workspace_id,
            )
        )
        doc = res.scalar_one_or_none()
        if not doc:
            raise DocumentNotFoundError(f"Document {document_id} not found.")

        if doc.status not in ("FAILED", "PENDING"):
            raise DocumentStateError(
                f"Cannot retry document in status '{doc.status}'. Only FAILED or PENDING documents can be retried."
            )

        doc.status = "PENDING"
        doc.error_message = None
        doc.processing_heartbeat = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(doc)

        # Execute processing
        return await cls.process_document(db, storage, document_id, workspace_id)
