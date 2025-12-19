"""Idempotent migration to add helpful indexes on payment_requests filters.

Adds indexes for the most common filter columns used by list/review pages:
- status
- project_id
- supplier_id
- created_at
- updated_at
- submitted_to_pm_at
"""
import os
import sys
from contextlib import closing

import psycopg2


LOG_PREFIX = "[payment_request_indexes]"

INDEX_DEFINITIONS = [
    ("idx_payment_requests_status", "status"),
    ("idx_payment_requests_project_id", "project_id"),
    ("idx_payment_requests_supplier_id", "supplier_id"),
    ("idx_payment_requests_created_at", "created_at"),
    ("idx_payment_requests_updated_at", "updated_at"),
    ("idx_payment_requests_submitted_to_pm_at", "submitted_to_pm_at"),
]


def log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}")
    sys.stdout.flush()


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return url


def index_exists(cursor, name: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_indexes
            WHERE schemaname = ANY (current_schemas(false))
              AND indexname = %s
        );
        """,
        (name,),
    )
    exists = cursor.fetchone()[0]
    return bool(exists)


def column_exists(cursor, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = %s
              AND column_name = %s
              AND table_schema = ANY (current_schemas(false))
        );
        """,
        ("payment_requests", column_name),
    )
    exists = cursor.fetchone()[0]
    return bool(exists)


def create_index(cursor, name: str, column: str) -> None:
    log(f"Creating index {name} on payment_requests({column})...")
    cursor.execute(
        f"CREATE INDEX {name} ON payment_requests ({column});"
    )


def ensure_indexes(cursor) -> None:
    for name, column in INDEX_DEFINITIONS:
        if not column_exists(cursor, column):
            log(f"Column payment_requests.{column} is missing; skipping index {name}.")
            continue
        if index_exists(cursor, name):
            log(f"Index {name} already exists; skipping.")
            continue
        create_index(cursor, name, column)


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
                ensure_indexes(cursor)
                log("Migration completed successfully.")
    except psycopg2.Error as exc:
        log(f"Migration failed: {exc.pgerror or exc}")
        sys.exit(1)
    except Exception as exc:  # pragma: no cover - defensive
        log(f"Unexpected migration failure: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
