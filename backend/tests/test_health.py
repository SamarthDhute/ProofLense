import pytest
from httpx import AsyncClient

from backend.app.core.config import settings


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    """Test health check returns HTTP 200, healthy DB & storage, and canonical 5-tuple config."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "healthy"
    assert data["storage"] == "healthy"

    # Canonical 5-tuple embedding config verification
    cfg = data["active_embedding_config"]
    assert cfg["provider"] == settings.EMBEDDING_PROVIDER
    assert cfg["model"] == settings.EMBEDDING_MODEL
    assert cfg["version"] == settings.EMBEDDING_VERSION
    assert cfg["dimensions"] == settings.EMBEDDING_DIMENSIONS
    assert cfg["task_type"] == settings.EMBEDDING_TASK_TYPE
    assert cfg["task_type"] == "RETRIEVAL_DOCUMENT"
