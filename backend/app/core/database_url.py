import re
import urllib.parse
from typing import Any, Dict, Optional


def redact_db_url(url: str) -> str:
    """Safely redact passwords and secrets from database URLs for logging and display."""
    if not url:
        return ""
    try:
        parsed = parse_db_url(url)
        scheme = parsed["scheme"]
        user = parsed["user"]
        has_pass = bool(parsed["password"])
        host = parsed["host"]
        port_part = f":{parsed['port']}" if parsed["port"] else ""
        dbname_part = f"/{parsed['dbname']}" if parsed["dbname"] else ""
        query_part = f"?{urllib.parse.urlencode(parsed['query_params'])}" if parsed["query_params"] else ""

        auth = ""
        if user and has_pass:
            auth = f"{user}:***@"
        elif user:
            auth = f"{user}@"

        return f"{scheme}://{auth}{host}{port_part}{dbname_part}{query_part}"
    except Exception:
        # Fallback regex replacing password between colon and @
        return re.sub(r":([^/@:]+)@", ":***@", url)


def redact_db_error(error: Any) -> str:
    """Sanitize database exception messages, removing connection strings and credentials."""
    if error is None:
        return ""
    err_str = str(error)
    # Mask password patterns like password='...', password="...", pwd=...
    err_str = re.sub(r"(password\s*=\s*['\"])[^'\"]+(['\"])", r"\1***\2", err_str, flags=re.IGNORECASE)
    # Mask embedded URLs in error tracebacks
    err_str = re.sub(r"([a-zA-Z0-9+_]+://[^:@\s]+:)[^@\s]+(@)", r"\1***\2", err_str)
    # Mask Supabase bearer tokens / pooler keys if present
    err_str = re.sub(r"(sbp_[a-zA-Z0-9_]+)", r"[REDACTED_SUPABASE_TOKEN]", err_str)
    return err_str


def parse_db_url(url: str) -> Dict[str, Any]:
    """Parse database URL into its logical components, safely handling special characters."""
    if not url or not isinstance(url, str):
        raise ValueError("Database URL must be a non-empty string.")

    pattern = (
        r"^([a-zA-Z0-9+_]+)://"  # scheme
        r"(?:([^:@]+)(?::(.*))?@)?"  # user, password (greedy match for password allows @, #, etc.)
        r"(\[[a-fA-F0-9:]+\]|[^:/?#]+)"  # host (supports IPv6 brackets or hostname)
        r"(?::([0-9]+))?"  # port
        r"(?:/([^?#]*))?"  # database
        r"(?:\?(.*))?$"  # query
    )
    match = re.match(pattern, url.strip())
    if not match:
        raise ValueError(f"Invalid database URL structure: {redact_db_url(url)}")

    scheme, user, password, host, port, dbname, query = match.groups()

    if not (scheme.startswith("postgres") or scheme.startswith("postgresql")):
        raise ValueError(f"Unsupported database scheme '{scheme}'. Expected postgresql:// or postgres://.")

    # Parse query parameters safely
    query_params = {}
    if query:
        query_params = dict(urllib.parse.parse_qsl(query))

    return {
        "scheme": scheme.lower(),
        "user": user or "",
        "password": password or "",
        "host": host,
        "port": int(port) if port else None,
        "dbname": dbname or "",
        "query_params": query_params,
    }


def normalize_database_url(
    url: str,
    driver: str = "asyncpg",
    ssl_mode: Optional[str] = None,
) -> str:
    """Safely normalize a database URL for the target SQLAlchemy or Alembic driver.
    
    Args:
        url: Raw database connection string (e.g. from Supabase dashboard).
        driver: Target driver ('asyncpg' for async SQLAlchemy, 'psycopg' for Alembic/sync).
        ssl_mode: Explicit SSL mode override (e.g. 'require').
    
    Returns:
        Normalized, driver-compatible, and safely encoded database URL.
    """
    parsed = parse_db_url(url)
    user = parsed["user"]
    password = parsed["password"]
    host = parsed["host"]
    port = parsed["port"]
    dbname = parsed["dbname"]
    query_params = dict(parsed["query_params"])

    # Determine SSL setting
    effective_ssl = ssl_mode or query_params.get("sslmode") or query_params.get("ssl")
    if not effective_ssl and ("supabase.co" in host or "supabase.com" in host):
        effective_ssl = "require"

    # URL-encode credentials to avoid syntax ambiguities with special characters (@, #, :, %)
    user_encoded = urllib.parse.quote(user, safe="") if user else ""
    password_encoded = urllib.parse.quote(password, safe="") if password else ""

    auth_part = ""
    if user_encoded and password_encoded:
        auth_part = f"{user_encoded}:{password_encoded}@"
    elif user_encoded:
        auth_part = f"{user_encoded}@"

    port_part = f":{port}" if port else ""
    netloc = f"{auth_part}{host}{port_part}"
    path = f"/{dbname}" if dbname else "/"

    if driver == "asyncpg":
        scheme = "postgresql+asyncpg"
        # asyncpg does not accept 'sslmode' query param; remove it to prevent connection errors
        query_params.pop("sslmode", None)
        if effective_ssl:
            # asyncpg accepts ssl=<mode>
            query_params["ssl"] = effective_ssl
    elif driver in ("psycopg", "sync"):
        scheme = "postgresql+psycopg"
        # psycopg v3 natively supports sslmode=<mode>
        query_params.pop("ssl", None)
        if effective_ssl:
            query_params["sslmode"] = effective_ssl
    else:
        raise ValueError(f"Unsupported driver target '{driver}'. Choose 'asyncpg' or 'psycopg'.")

    query_str = urllib.parse.urlencode(query_params) if query_params else ""
    final_url = f"{scheme}://{netloc}{path}"
    if query_str:
        final_url = f"{final_url}?{query_str}"

    return final_url


def is_supabase_host(host: Optional[str]) -> bool:
    """Check if the configured host belongs to a Supabase project."""
    if not host:
        return False
    return "supabase.co" in host or "supabase.com" in host
