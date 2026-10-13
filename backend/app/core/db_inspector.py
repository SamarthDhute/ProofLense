from typing import Any, Dict, List, Optional
import psycopg
from sqlalchemy.engine import make_url

from backend.app.core.config import settings
from backend.app.core.database_url import is_supabase_host, redact_db_error, redact_db_url


REQUIRED_TABLES = [
    "workspaces",
    "documents",
    "document_pages",
    "document_chunks",
    "conversations",
    "messages",
    "comparisons",
    "comparison_documents",
]


def inspect_target_database() -> Dict[str, Any]:
    """Inspect the target database connection, existing schema, Alembic revision, and pgvector readiness.
    
    Safe read-only diagnostics. Never alters schema or drops data.
    """
    url = settings.sync_database_url
    redacted_url = redact_db_url(url)
    parsed = make_url(url)
    host = parsed.host or "unknown"
    is_sb = is_supabase_host(host)

    report: Dict[str, Any] = {
        "target_url": redacted_url,
        "host": host,
        "is_supabase": is_sb,
        "connected": False,
        "db_version": None,
        "pgvector_available": False,
        "pgvector_installed": False,
        "existing_tables": [],
        "alembic_version": None,
        "status": "UNKNOWN",
        "action_required": None,
        "error": None,
    }

    try:
        # Connect using psycopg with timeout
        # Convert SQLAlchemy URL query to psycopg parameters
        conn_kwargs: Dict[str, Any] = {
            "host": parsed.host,
            "port": parsed.port or 5432,
            "dbname": parsed.database or "postgres",
            "user": parsed.username or "postgres",
            "password": parsed.password or "",
            "connect_timeout": 5,
        }
        if "sslmode" in parsed.query:
            conn_kwargs["sslmode"] = parsed.query["sslmode"]
        elif is_sb:
            conn_kwargs["sslmode"] = "require"

        with psycopg.connect(**conn_kwargs) as conn:
            report["connected"] = True
            with conn.cursor() as cur:
                # 1. Database version
                cur.execute("SELECT version();")
                row = cur.fetchone()
                if row:
                    report["db_version"] = row[0]

                # 2. Check pgvector extension availability & installation
                cur.execute("SELECT default_version, installed_version FROM pg_available_extensions WHERE name = 'vector';")
                vec_row = cur.fetchone()
                if vec_row:
                    report["pgvector_available"] = True
                    report["pgvector_installed"] = bool(vec_row[1])

                # 3. Existing tables in public schema
                cur.execute("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = 'public' ORDER BY table_name;
                """)
                tables = [r[0] for r in cur.fetchall()]
                report["existing_tables"] = tables

                # 4. Alembic version
                if "alembic_version" in tables:
                    cur.execute("SELECT version_num FROM alembic_version;")
                    alembic_row = cur.fetchone()
                    if alembic_row:
                        report["alembic_version"] = alembic_row[0]

        # Determine migration status
        tables_set = set(report["existing_tables"])
        required_set = set(REQUIRED_TABLES)

        if not tables_set or tables_set == {"alembic_version"}:
            report["status"] = "EMPTY_READY"
            report["action_required"] = "Database is clean. Ready to execute 'alembic upgrade head'."
        elif required_set.issubset(tables_set):
            if report["alembic_version"] == "001_initial_schema":
                report["status"] = "ALREADY_MIGRATED"
                report["action_required"] = "Schema is fully initialized and matches current revision (001_initial_schema)."
            else:
                report["status"] = "SCHEMA_PRESENT_REVISION_MISMATCH"
                report["action_required"] = "Tables exist but alembic_version differs. Manual verification required."
        else:
            missing = list(required_set - tables_set)
            overlapping = list(required_set.intersection(tables_set))
            report["status"] = "PARTIAL_CONFLICT"
            report["action_required"] = (
                f"Partial schema detected. Existing: {overlapping}. Missing: {missing}. "
                "Human inspection required to prevent data loss."
            )

    except Exception as exc:
        report["error"] = redact_db_error(exc)
        report["status"] = "CONNECTION_FAILED"
        report["action_required"] = "Check host resolution, pooler credentials, and network/firewall settings."

    return report


if __name__ == "__main__":
    import json
    rep = inspect_target_database()
    print(json.dumps(rep, indent=2))
