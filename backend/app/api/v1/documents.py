from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.deps import get_authenticated_workspace, get_storage_service
from backend.app.core.database import get_db
from backend.app.models.workspace import Workspace
from backend.app.schemas.document import (
    DocumentDeleteResponse,
    DocumentDetailResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentUploadResponse,
    RecoveryItemResult,
    RecoveryResponse,
)
from backend.app.services.document_service import (
    DocumentDuplicateError,
    DocumentNotFoundError,
    DocumentService,
    DocumentStateError,
    DocumentValidationError,
)
from backend.app.services.pdf_extractor import (
    PdfCorruptedError,
    PdfPasswordProtectedError,
    PdfScannedError,
    PdfValidationError,
)
from backend.app.services.recovery_service import RecoveryService
from backend.app.storage.base import StorageService

router = APIRouter(prefix="/documents", tags=["Documents"])


async def _background_process_document(
    document_id: UUID,
    workspace_id: UUID,
):
    """Background task wrapper creating isolated DB session for processing."""
    from backend.app.core.database import AsyncSessionLocal as async_session_factory
    storage = get_storage_service()
    async with async_session_factory() as session:
        try:
            await DocumentService.process_document(
                db=session,
                storage=storage,
                document_id=document_id,
                workspace_id=workspace_id,
            )
        except Exception:
            pass


@router.post(
    "",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and register a PDF document",
)
async def upload_document(
    file: UploadFile = File(...),
    sync: bool = Query(
        False,
        description="If True, processes document extraction and chunking synchronously before returning response.",
    ),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    workspace: Workspace = Depends(get_authenticated_workspace),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    """Upload a PDF, validate format and integrity, store raw file, and initiate extraction pipeline."""
    try:
        doc = await DocumentService.upload_document(
            db=db,
            storage=storage,
            workspace_id=workspace.id,
            file=file,
        )
    except DocumentValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except DocumentDuplicateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except (PdfPasswordProtectedError, PdfCorruptedError, PdfValidationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except PdfScannedError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document upload failed: {str(exc)}",
        )

    if sync:
        # Synchronous execution (useful for deterministic tests and pipelines)
        try:
            doc = await DocumentService.process_document(
                db=db,
                storage=storage,
                document_id=doc.id,
                workspace_id=workspace.id,
                raise_on_error=True,
            )
        except (PdfPasswordProtectedError, PdfCorruptedError, PdfValidationError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        except PdfScannedError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    else:
        # Asynchronous background execution
        background_tasks.add_task(
            _background_process_document,
            document_id=doc.id,
            workspace_id=workspace.id,
        )

    return DocumentUploadResponse(
        id=doc.id,
        workspace_id=doc.workspace_id,
        filename=doc.filename,
        file_hash=doc.file_hash,
        mime_type=doc.mime_type,
        status=doc.status,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List workspace documents with pagination",
)
async def list_documents(
    skip: int = Query(0, ge=0, description="Offset items"),
    limit: int = Query(20, ge=1, le=100, description="Limit items per page"),
    workspace: Workspace = Depends(get_authenticated_workspace),
    db: AsyncSession = Depends(get_db),
):
    """List documents belonging to the authenticated workspace with page and chunk counts."""
    items, total = await DocumentService.list_documents(
        db=db,
        workspace_id=workspace.id,
        skip=skip,
        limit=limit,
    )
    return DocumentListResponse(
        items=[DocumentListItem(**item) for item in items],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
    summary="Get document metadata and processing status",
)
async def get_document(
    document_id: UUID,
    workspace: Workspace = Depends(get_authenticated_workspace),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve document status, page count, and chunk count, strictly isolated to the caller workspace."""
    doc_data = await DocumentService.get_document(
        db=db,
        workspace_id=workspace.id,
        document_id=document_id,
    )
    if not doc_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found in this workspace.",
        )
    return DocumentDetailResponse(**doc_data)


@router.delete(
    "/{document_id}",
    response_model=DocumentDeleteResponse,
    summary="Durable idempotent document deletion",
)
async def delete_document(
    document_id: UUID,
    workspace: Workspace = Depends(get_authenticated_workspace),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document, its storage object, and all associated pages and chunks idempotently."""
    try:
        await DocumentService.delete_document(
            db=db,
            storage=storage,
            workspace_id=workspace.id,
            document_id=document_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Durable deletion failed: {str(exc)}",
        )

    return DocumentDeleteResponse(
        id=document_id,
        deleted=True,
        message="Document and all associated storage/database artifacts deleted successfully.",
    )


@router.post(
    "/{document_id}/retry",
    response_model=DocumentDetailResponse,
    summary="Retry a failed document ingestion job",
)
async def retry_document(
    document_id: UUID,
    workspace: Workspace = Depends(get_authenticated_workspace),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    """Reset a FAILED document to PENDING and trigger re-processing."""
    try:
        doc = await DocumentService.retry_document_processing(
            db=db,
            storage=storage,
            workspace_id=workspace.id,
            document_id=document_id,
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found in this workspace.",
        )
    except DocumentStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retry failed: {str(exc)}",
        )

    # Return refreshed detail
    doc_data = await DocumentService.get_document(db, workspace.id, doc.id)
    return DocumentDetailResponse(**doc_data)


@router.post(
    "/recovery/stale-jobs",
    response_model=RecoveryResponse,
    summary="Recover interrupted or crashed document ingestion jobs",
)
async def trigger_recovery(
    grace_period_seconds: Optional[int] = Query(
        None,
        description="Lease timeout in seconds to consider an in-flight job stale.",
    ),
    workspace: Workspace = Depends(get_authenticated_workspace),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    """Scan and recover stale jobs stuck in PENDING, EXTRACTING, or CHUNKING strictly within caller workspace."""
    summary = await RecoveryService.recover_stale_documents(
        db=db,
        storage=storage,
        workspace_id=workspace.id,
        grace_period_seconds=grace_period_seconds,
    )
    return RecoveryResponse(
        recovered_count=summary.recovered_count,
        permanently_failed_count=summary.permanently_failed_count,
        results=[
            RecoveryItemResult(
                document_id=r.document_id,
                action=r.action,
                status=r.status,
                error=r.error,
            )
            for r in summary.results
        ],
    )
