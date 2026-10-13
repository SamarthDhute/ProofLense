import os
import shutil
import tempfile
from typing import AsyncGenerator
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.main import app
from backend.app.storage.local import LocalStorageService
from backend.app.api.deps import get_storage_service


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated database session with NullPool per test."""
    test_engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        poolclass=NullPool,
    )
    test_session_local = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with test_session_local() as session:
        yield session
        await session.rollback()
    await test_engine.dispose()


@pytest.fixture(scope="function")
def temp_storage():
    """Create a temporary storage directory for tests and clean up afterwards."""
    temp_dir = tempfile.mkdtemp(prefix="prooflens_test_storage_")
    storage_service = LocalStorageService(temp_dir)
    yield storage_service
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest_asyncio.fixture(scope="function")
async def client(temp_storage, db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Async test client with dependency overrides."""
    async def override_get_db():
        yield db_session

    def override_get_storage():
        return temp_storage

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage_service] = override_get_storage

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
