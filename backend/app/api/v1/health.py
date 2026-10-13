import os
from fastapi import APIRouter, Depends, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.database_url import redact_db_error
from backend.app.schemas.health import HealthResponse

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Service health and readiness check",
)
async def check_health(
    db: AsyncSession = Depends(get_db),
):
    """Verify application liveness, database connectivity, storage writability, and configuration.
    
    Database errors are strictly sanitized to prevent credential and connection string leakage.
    """
    liveness_status = "healthy"
    db_status = "healthy"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"unhealthy: {redact_db_error(exc)}"

    storage_status = "healthy"
    try:
        os.makedirs(settings.STORAGE_LOCAL_ROOT, exist_ok=True)
        test_path = os.path.join(settings.STORAGE_LOCAL_ROOT, ".health_check")
        with open(test_path, "w") as f:
            f.write("ok")
        if os.path.exists(test_path):
            os.remove(test_path)
    except Exception as exc:
        storage_status = f"unhealthy: {str(exc)}"

    overall_status = "healthy" if db_status == "healthy" and storage_status == "healthy" else "unhealthy"

    return HealthResponse(
        status=overall_status,
        liveness=liveness_status,
        database=db_status,
        database_provider="supabase" if settings.is_supabase else "postgresql",
        storage=storage_status,
        storage_type="local",
        active_embedding_config=settings.ACTIVE_EMBEDDING_CONFIG._asdict(),
        environment=settings.ENVIRONMENT,
        version="1.0.0",
    )
