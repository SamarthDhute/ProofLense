import pytest
from sqlalchemy.engine import make_url

from backend.app.core.database_url import (
    is_supabase_host,
    normalize_database_url,
    parse_db_url,
    redact_db_error,
    redact_db_url,
)


def test_supabase_url_normalization_asyncpg():
    """Verify standard Supabase dashboard connection URL normalizes safely to asyncpg driver."""
    raw_url = "postgresql://postgres.myproj:SecretPass123@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    normalized = normalize_database_url(raw_url, driver="asyncpg")

    assert normalized.startswith("postgresql+asyncpg://")
    parsed = make_url(normalized)
    assert parsed.username == "postgres.myproj"
    assert parsed.password == "SecretPass123"
    assert parsed.host == "aws-0-ap-south-1.pooler.supabase.com"
    assert parsed.port == 5432
    assert parsed.database == "postgres"
    # asyncpg query param ssl=require should be present for Supabase host
    assert parsed.query.get("ssl") == "require"
    # sslmode must be stripped to prevent asyncpg TypeError
    assert "sslmode" not in parsed.query


def test_supabase_url_normalization_psycopg():
    """Verify standard Supabase connection URL normalizes safely to psycopg (Alembic) driver."""
    raw_url = "postgresql://postgres.myproj:SecretPass123@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    normalized = normalize_database_url(raw_url, driver="psycopg")

    assert normalized.startswith("postgresql+psycopg://")
    parsed = make_url(normalized)
    assert parsed.username == "postgres.myproj"
    assert parsed.password == "SecretPass123"
    assert parsed.host == "aws-0-ap-south-1.pooler.supabase.com"
    assert parsed.port == 5432
    assert parsed.database == "postgres"
    # psycopg natively accepts sslmode
    assert parsed.query.get("sslmode") == "require"
    assert "ssl" not in parsed.query


def test_safe_handling_of_special_characters_in_credentials():
    """Verify passwords containing special characters (@, #, :, %, !) are preserved and encoded."""
    complex_password = "P@ss#w:ord%99!Key"
    raw_url = f"postgresql://postgres.tenant:{complex_password}@aws-0-pooler.supabase.com:5432/postgres"

    # Normalize for asyncpg
    norm_async = normalize_database_url(raw_url, driver="asyncpg")
    sa_async = make_url(norm_async)
    assert sa_async.username == "postgres.tenant"
    assert sa_async.password == complex_password

    # Normalize for psycopg
    norm_sync = normalize_database_url(raw_url, driver="psycopg")
    sa_sync = make_url(norm_sync)
    assert sa_sync.username == "postgres.tenant"
    assert sa_sync.password == complex_password


def test_missing_or_invalid_database_url():
    """Verify invalid or empty database URL raises clear ValueError."""
    with pytest.raises(ValueError):
        parse_db_url("")

    with pytest.raises(ValueError):
        parse_db_url("not_a_valid_url")

    with pytest.raises(ValueError):
        normalize_database_url("http://invalid_db_scheme.com", driver="asyncpg")


def test_secret_redaction_in_urls():
    """Verify database password is never exposed in redacted URLs."""
    raw_url = "postgresql+asyncpg://postgres.tenant_id:UltraSecretPassword123@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?ssl=require"
    redacted = redact_db_url(raw_url)

    assert "UltraSecretPassword123" not in redacted
    assert "postgres.tenant_id:***@" in redacted
    assert "aws-0-ap-south-1.pooler.supabase.com" in redacted


def test_secret_redaction_in_errors():
    """Verify exception messages containing passwords or credentials are scrubbed."""
    raw_secret = "SupabaseSuperSecretPassword99"
    error_msg = f"asyncpg.exceptions.InvalidPasswordError: password authentication failed for user 'postgres.proj' with password='{raw_secret}'"

    sanitized = redact_db_error(error_msg)
    assert raw_secret not in sanitized
    assert "password='***'" in sanitized

    # Embedded URL in error
    url_error = f"Failed to connect to postgresql://postgres:{raw_secret}@db.supabase.co:5432/postgres"
    sanitized_url = redact_db_error(url_error)
    assert raw_secret not in sanitized_url
    assert ":***@" in sanitized_url


def test_ssl_configuration_modes():
    """Verify custom SSL mode parameter override is applied correctly."""
    url = "postgresql://user:pass@myhost:5432/mydb"
    async_url = normalize_database_url(url, driver="asyncpg", ssl_mode="verify-full")
    assert "ssl=verify-full" in async_url

    sync_url = normalize_database_url(url, driver="psycopg", ssl_mode="verify-full")
    assert "sslmode=verify-full" in sync_url


def test_supabase_host_detection():
    """Verify accurate detection of Supabase hostnames."""
    assert is_supabase_host("aws-0-ap-south-1.pooler.supabase.com") is True
    assert is_supabase_host("db.myprojectref.supabase.co") is True
    assert is_supabase_host("localhost") is False
    assert is_supabase_host("127.0.0.1") is False
    assert is_supabase_host("") is False
    assert is_supabase_host(None) is False


def test_missing_database_url_in_non_test_environment_fails():
    """Verify application fails closed when DATABASE_URL is missing in non-test environments."""
    from backend.app.core.config import Settings

    with pytest.raises(ValueError, match="DATABASE_URL is not configured"):
        Settings(ENVIRONMENT="production", DATABASE_URL=None, _env_file=None)

    with pytest.raises(ValueError, match="DATABASE_URL is not configured"):
        Settings(ENVIRONMENT="development", DATABASE_URL="", _env_file=None)


def test_missing_database_url_in_test_environment_defaults_isolated_db():
    """Verify test environment safely defaults to isolated test database when omitted."""
    from backend.app.core.config import Settings

    settings_test = Settings(ENVIRONMENT="test", DATABASE_URL=None, _env_file=None)
    assert settings_test.DATABASE_URL == "postgresql+asyncpg://postgres:1234@localhost:5432/prooflens"

