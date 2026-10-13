from abc import ABC, abstractmethod
from typing import List


class EmbeddingDimensionMismatchError(ValueError):
    """Raised when generated embedding vector dimensions do not match target schema dimensions."""
    pass


class EmbeddingProvider(ABC):
    """Abstract interface for document and query embedding providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        pass

    @property
    @abstractmethod
    def dimensions(self) -> int:
        pass

    @property
    @abstractmethod
    def task_type(self) -> str:
        pass

    @abstractmethod
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of document chunks using RETRIEVAL_DOCUMENT role."""
        pass

    @abstractmethod
    async def embed_query(self, query: str) -> List[float]:
        """Embed a search query using RETRIEVAL_QUERY role."""
        pass

    def validate_dimensions(self, target_dimensions: int) -> None:
        """Fail-closed validation against target database/index dimensions."""
        if self.dimensions != target_dimensions:
            raise EmbeddingDimensionMismatchError(
                f"Embedding provider '{self.provider_name}:{self.model_name}' produces "
                f"{self.dimensions} dimensions, but target index requires {target_dimensions} dimensions."
            )


class LocalFallbackEmbeddingProvider(EmbeddingProvider):
    """Local fallback provider stub/wrapper (e.g. for FastEmbed bge-small-en-v1.5).
    
    Fixed dimensions: 384.
    Used for local testing, fallback offline mode, and verifying dimension mismatch guards.
    """

    def __init__(self, model_name: str = "bge-small-en-v1.5", dimensions: int = 384):
        self._model_name = model_name
        self._dimensions = dimensions
        self._version = "v1"
        self._task_type = "RETRIEVAL_DOCUMENT"

    @property
    def provider_name(self) -> str:
        return "fastembed"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def task_type(self) -> str:
        return self._task_type

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        # Deterministic normalized synthetic embeddings for unit testing & offline mode
        # without requiring heavy multi-GB torch downloads during Phase 1
        embeddings = []
        for text in texts:
            # Generate deterministic vector based on text length and content hash
            val = float(len(text) % 100) / 100.0
            vec = [val] * self._dimensions
            embeddings.append(vec)
        return embeddings

    async def embed_query(self, query: str) -> List[float]:
        val = float(len(query) % 100) / 100.0
        return [val] * self._dimensions
