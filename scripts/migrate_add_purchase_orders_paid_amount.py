"""Idempotent migration to add purchase_orders.paid_amount."""
import os
import sys
from contextlib import closing

import psycopg2


LOG_PREFIX = "[purchase_orders_paid_amount]"


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
    return bool(cursor.fetchone()[0])


def column_exists(cursor, table_name: str, column_name: str) -> bool:
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
        (table_name, column_name),
    )
    return bool(cursor.fetchone()[0])


def add_column(cursor) -> None:
    log("Adding purchase_orders.paid_amount column...")
    cursor.execute(
        """
        ALTER TABLE purchase_orders
        ADD COLUMN paid_amount NUMERIC(14,2) NOT NULL DEFAULT 0;
        """
    )


def backfill_nulls(cursor) -> int:
    cursor.execute(
        """
        UPDATE purchase_orders
        SET paid_amount = 0
        WHERE paid_amount IS NULL;
        """
    )
    return cursor.rowcount


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
                if not table_exists(cursor, "purchase_orders"):
                    log("purchase_orders table is missing; cannot apply migration.")
                    sys.exit(1)

                if not column_exists(cursor, "purchase_orders", "paid_amount"):
                    add_column(cursor)
                else:
                    log("Column purchase_orders.paid_amount already exists; skipping add.")

                updated = backfill_nulls(cursor)
                if updated:
                    log(f"Backfilled {updated} purchase_orders rows with paid_amount = 0.")
                else:
                    log("No NULL paid_amount rows to backfill.")

                log("Migration completed successfully.")
    except psycopg2.Error as exc:
        log(f"Migration failed: {exc.pgerror or exc}")
        sys.exit(1)
    except Exception as exc:  # pragma: no cover - defensive
        log(f"Unexpected migration failure: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
