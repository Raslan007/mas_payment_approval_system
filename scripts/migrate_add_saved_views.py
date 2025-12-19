"""Idempotent migration to create the saved_views table for per-user payment views."""
import os
import sys
from contextlib import closing

import psycopg2


LOG_PREFIX = "[saved_views_table]"

CREATE_TABLE_SQL = """
CREATE TABLE saved_views (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name VARCHAR(150) NOT NULL,
    endpoint VARCHAR(255) NOT NULL,
    query_string TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}")
    sys.stdout.flush()


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return url


def table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = %s
              AND table_schema = ANY (current_schemas(false))
        );
        """,
        (table_name,),
    )
    exists = cursor.fetchone()[0]
    return bool(exists)


def ensure_table(cursor) -> None:
    if table_exists(cursor, "saved_views"):
        log("Table saved_views already exists; skipping.")
        return

    log("Creating table saved_views...")
    cursor.execute(CREATE_TABLE_SQL)
    log("Table saved_views created.")


def main() -> None:
    try:
        database_url = get_database_url()
    except RuntimeError as exc:
        log(f"Migration aborted: {exc}")
        sys.exit(1)

    log("Connecting to database...")
    try:
        with closing(psycopg2.connect(database_url)) as conn:
            conn.autocommit = False
            with conn, conn.cursor() as cursor:
                ensure_table(cursor)
                log("Migration completed successfully.")
    except psycopg2.Error as exc:
        log(f"Migration failed: {exc.pgerror or exc}")
        sys.exit(1)
    except Exception as exc:  # pragma: no cover - defensive
        log(f"Unexpected migration failure: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
