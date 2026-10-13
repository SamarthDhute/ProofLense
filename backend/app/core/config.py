import os
from typing import NamedTuple
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:1234@localhost:5432/prooflens"
    SYNC_DATABASE_URL: str = "postgresql+psycopg2://postgres:1234@localhost:5432/prooflens"

    # Storage
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
        extra="ignore"
    )

    @property
    def ACTIVE_EMBEDDING_CONFIG(self) -> EmbeddingConfigTuple:
        return EmbeddingConfigTuple(
            provider=self.EMBEDDING_PROVIDER,
            model=self.EMBEDDING_MODEL,
            version=self.EMBEDDING_VERSION,
            dimensions=self.EMBEDDING_DIMENSIONS,
            task_type=self.EMBEDDING_TASK_TYPE,
        )


settings = Settings()
