import os
from typing import Any, Dict, NamedTuple, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.core.database_url import (
    is_supabase_host,
    normalize_database_url,
    parse_db_url,
    redact_db_url,
)


class EmbeddingConfigTuple(NamedTuple):
    provider: str
    model: str
    version: str
    dimensions: int
    task_type: str


class Settings(BaseSettings):
    PROJECT_NAME: str = "ProofLens AI"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False

    # Primary Supabase / PostgreSQL Database URLs
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:1234@localhost:5432/prooflens",
        description="Async SQLAlchemy database URL. Standard Supabase URLs (postgresql://) are auto-normalized.",
    )
    SYNC_DATABASE_URL: Optional[str] = Field(
        default=None,
        description="Synchronous migration URL for Alembic. If omitted, derived automatically from DATABASE_URL.",
    )
    DATABASE_SSL_MODE: str = Field(
        default="require",
        description="SSL mode for remote connections (require, disable, verify-full).",
    )

    # Local Storage Root
    STORAGE_LOCAL_ROOT: str = "./data/storage"

    # Active canonical 5-tuple embedding config
    EMBEDDING_PROVIDER: str = "google"
    EMBEDDING_MODEL: str = "gemini-embedding-2"
    EMBEDDING_VERSION: str = "v1"
    EMBEDDING_DIMENSIONS: int = 768
    EMBEDDING_TASK_TYPE: str = "RETRIEVAL_DOCUMENT"
    QUERY_TASK_TYPE: str = "RETRIEVAL_QUERY"

    # Retrieval and LLM
    SIMILARITY_THRESHOLD: float = 0.75
    TOP_K_RETRIEVAL: int = 5
    GEMINI_API_KEY: str = ""
    LLM_MODEL: str = "gemini-2.5-flash"

    # Operational limits
    MAX_DOCUMENT_SIZE_MB: int = 50
    INGESTION_LEASE_TIMEOUT_SECONDS: int = 900  # 15 minutes

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def async_database_url(self) -> str:
        """Normalized asynchronous database URL for asyncpg engine."""
        return normalize_database_url(
            self.DATABASE_URL,
            driver="asyncpg",
            ssl_mode=self.DATABASE_SSL_MODE if self.is_supabase else None,
        )

    @property
    def sync_database_url(self) -> str:
        """Normalized synchronous database URL for Alembic migrations and psycopg."""
        raw_sync = self.SYNC_DATABASE_URL or self.DATABASE_URL
        return normalize_database_url(
            raw_sync,
            driver="psycopg",
            ssl_mode=self.DATABASE_SSL_MODE if self.is_supabase else None,
        )

    @property
    def is_supabase(self) -> bool:
        """Check if configured database host is a Supabase project."""
        try:
            parsed = parse_db_url(self.DATABASE_URL)
            return is_supabase_host(parsed.get("host"))
        except Exception:
            return False

    @property
    def ACTIVE_EMBEDDING_CONFIG(self) -> EmbeddingConfigTuple:
        return EmbeddingConfigTuple(
            provider=self.EMBEDDING_PROVIDER,
            model=self.EMBEDDING_MODEL,
            version=self.EMBEDDING_VERSION,
            dimensions=self.EMBEDDING_DIMENSIONS,
            task_type=self.EMBEDDING_TASK_TYPE,
        )

    def get_redacted_summary(self) -> Dict[str, Any]:
        """Safe configuration summary with all passwords and secrets redacted."""
        return {
            "project_name": self.PROJECT_NAME,
            "environment": self.ENVIRONMENT,
            "database_url": redact_db_url(self.DATABASE_URL),
            "sync_database_url": redact_db_url(self.sync_database_url),
            "is_supabase": self.is_supabase,
            "storage_local_root": self.STORAGE_LOCAL_ROOT,
            "active_embedding_config": self.ACTIVE_EMBEDDING_CONFIG._asdict(),
            "gemini_api_key_configured": bool(self.GEMINI_API_KEY.strip()),
        }


settings = Settings()
