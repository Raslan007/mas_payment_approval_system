"""Idempotent migration script to add payment_requests.updated_at."""
import os
import sys
from contextlib import closing

import psycopg2


def log(message: str) -> None:
    """Simple stdout logger."""
    print(message)
    sys.stdout.flush()


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return url


def column_exists(cursor) -> bool:
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
        ("payment_requests", "updated_at"),
    )
    exists = cursor.fetchone()[0]
    return bool(exists)


def add_column(cursor) -> None:
    log("Adding updated_at column to payment_requests...")
    cursor.execute(
        """
        ALTER TABLE payment_requests
        ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW();
        """
    )


def backfill_column(cursor) -> None:
    log("Backfilling updated_at from created_at where available...")
    cursor.execute(
        """
        UPDATE payment_requests
        SET updated_at = created_at
        WHERE updated_at IS NULL AND created_at IS NOT NULL;
        """
    )
    log("Setting updated_at to current timestamp for any remaining null rows...")
    cursor.execute(
        """
        UPDATE payment_requests
        SET updated_at = NOW()
        WHERE updated_at IS NULL;
        """
    )


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
                if column_exists(cursor):
                    log("Column payment_requests.updated_at already exists; nothing to do.")
                    return

                add_column(cursor)
                backfill_column(cursor)
                log("Migration completed successfully.")
    except psycopg2.Error as exc:
        log(f"Migration failed: {exc.pgerror or exc}")
        sys.exit(1)
    except Exception as exc:  # pragma: no cover - defensive
        log(f"Unexpected migration failure: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
