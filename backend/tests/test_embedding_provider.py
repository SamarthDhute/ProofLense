import pytest

from backend.app.services.embedding_provider import (
    EmbeddingDimensionMismatchError,
    LocalFallbackEmbeddingProvider,
)
from backend.app.core.gemini_client import (
    GeminiConfigurationError,
    get_gemini_client,
)


@pytest.mark.asyncio
async def test_fallback_provider_interface():
    """Verify fallback embedding provider properties and 5-tuple matching."""
    provider = LocalFallbackEmbeddingProvider()
    assert provider.provider_name == "fastembed"
    assert provider.model_name == "bge-small-en-v1.5"
    assert provider.version == "v1"
    assert provider.dimensions == 384
    assert provider.task_type == "RETRIEVAL_DOCUMENT"


@pytest.mark.asyncio
async def test_fallback_provider_embedding_generation():
    """Verify fallback provider produces normalized vectors of expected dimension."""
    provider = LocalFallbackEmbeddingProvider(dimensions=384)
    chunks = ["ProofLens AI first document test chunk", "Second test chunk"]
    docs_embeddings = await provider.embed_documents(chunks)
    assert len(docs_embeddings) == 2
    assert len(docs_embeddings[0]) == 384
    assert len(docs_embeddings[1]) == 384

    query_vec = await provider.embed_query("search query")
    assert len(query_vec) == 384


def test_dimension_validation_fail_closed():
    """Verify that dimension mismatch triggers fail-closed error."""
    provider = LocalFallbackEmbeddingProvider(dimensions=384)
    # Validating matching dimensions succeeds
    provider.validate_dimensions(384)

    # Validating against 768 pgvector schema must raise EmbeddingDimensionMismatchError
    with pytest.raises(EmbeddingDimensionMismatchError) as exc_info:
        provider.validate_dimensions(768)
    assert "produces 384 dimensions, but target index requires 768 dimensions" in str(exc_info.value)


def test_gemini_client_missing_key_security():
    """Verify that client initialization without an API key raises GeminiConfigurationError."""
    with pytest.raises(GeminiConfigurationError) as exc_info:
        get_gemini_client(api_key="")
    assert "GEMINI_API_KEY is not configured" in str(exc_info.value)
