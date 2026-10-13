import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_workspace_success(client: AsyncClient):
    """Test workspace creation returns ID, name, created_at, and capability_token."""
    response = await client.post(
        "/api/v1/workspaces",
        json={"name": "Research Workspace Alpha"},
    )
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["name"] == "Research Workspace Alpha"
    assert "capability_token" in data
    assert len(data["capability_token"]) >= 32
    assert "created_at" in data


@pytest.mark.asyncio
async def test_get_workspace_authenticated(client: AsyncClient):
    """Test retrieving workspace metadata with valid capability token."""
    create_res = await client.post(
        "/api/v1/workspaces",
        json={"name": "Protected Workspace"},
    )
    create_data = create_res.json()
    workspace_id = create_data["id"]
    token = create_data["capability_token"]

    # Authenticated GET
    get_res = await client.get(
        f"/api/v1/workspaces/{workspace_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["id"] == workspace_id
    assert get_data["name"] == "Protected Workspace"
    # Ensure raw capability token is NEVER returned in GET endpoint
    assert "capability_token" not in get_data


@pytest.mark.asyncio
async def test_get_workspace_missing_auth(client: AsyncClient):
    """Test retrieving workspace without Authorization header returns 401."""
    create_res = await client.post(
        "/api/v1/workspaces",
        json={"name": "Secured Workspace"},
    )
    workspace_id = create_res.json()["id"]

    res = await client.get(f"/api/v1/workspaces/{workspace_id}")
    assert res.status_code == 401
    assert "Missing Authorization header" in res.json()["detail"]


@pytest.mark.asyncio
async def test_get_workspace_invalid_token(client: AsyncClient):
    """Test retrieving workspace with invalid capability token returns 403."""
    create_res = await client.post(
        "/api/v1/workspaces",
        json={"name": "Secured Workspace"},
    )
    workspace_id = create_res.json()["id"]

    res = await client.get(
        f"/api/v1/workspaces/{workspace_id}",
        headers={"Authorization": "Bearer wrong_token_value_here"},
    )
    assert res.status_code == 403
    assert "Invalid or revoked capability token" in res.json()["detail"]


@pytest.mark.asyncio
async def test_get_workspace_nonexistent(client: AsyncClient):
    """Test retrieving non-existent workspace returns 404."""
    random_id = str(uuid.uuid4())
    res = await client.get(
        f"/api/v1/workspaces/{random_id}",
        headers={"Authorization": "Bearer some_dummy_token_value"},
    )
    assert res.status_code == 404
    assert "not found" in res.json()["detail"]


@pytest.mark.asyncio
async def test_rotate_token_success(client: AsyncClient):
    """Test rotating capability token invalidates old token and activates new token."""
    create_res = await client.post(
        "/api/v1/workspaces",
        json={"name": "Rotatable Workspace"},
    )
    create_data = create_res.json()
    workspace_id = create_data["id"]
    old_token = create_data["capability_token"]

    # Rotate token
    rotate_res = await client.post(
        f"/api/v1/workspaces/{workspace_id}/token/rotate",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert rotate_res.status_code == 200
    rotate_data = rotate_res.json()
    new_token = rotate_data["capability_token"]
    assert new_token != old_token
    assert len(new_token) >= 32

    # Old token must now fail with 403 Forbidden
    old_auth_res = await client.get(
        f"/api/v1/workspaces/{workspace_id}",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert old_auth_res.status_code == 403

    # New token must succeed with 200 OK
    new_auth_res = await client.get(
        f"/api/v1/workspaces/{workspace_id}",
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert new_auth_res.status_code == 200


@pytest.mark.asyncio
async def test_workspace_isolation(client: AsyncClient):
    """Test that a capability token from Workspace A cannot access Workspace B."""
    ws_a = (await client.post("/api/v1/workspaces", json={"name": "Workspace A"})).json()
    ws_b = (await client.post("/api/v1/workspaces", json={"name": "Workspace B"})).json()

    # Attempt to access Workspace B using Token A
    cross_access_res = await client.get(
        f"/api/v1/workspaces/{ws_b['id']}",
        headers={"Authorization": f"Bearer {ws_a['capability_token']}"},
    )
    assert cross_access_res.status_code == 403
