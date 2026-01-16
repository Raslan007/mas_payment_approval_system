"""Idempotent migration to add payment_requests.purchase_order_id and related FK/index."""
import os
import sys
from contextlib import closing

import psycopg2


LOG_PREFIX = "[payment_requests_purchase_order_id]"


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


def constraint_exists(cursor, name: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.table_constraints
            WHERE constraint_name = %s
              AND table_schema = ANY (current_schemas(false))
        );
        """,
        (name,),
    )
    return bool(cursor.fetchone()[0])


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
    return bool(cursor.fetchone()[0])


def add_column(cursor) -> None:
    log("Adding payment_requests.purchase_order_id column...")
    cursor.execute(
        """
        ALTER TABLE payment_requests
        ADD COLUMN purchase_order_id INTEGER;
        """
    )


def add_fk_constraint(cursor) -> None:
    log("Adding foreign key constraint fk_payment_requests_purchase_order_id...")
    cursor.execute(
        """
        ALTER TABLE payment_requests
        ADD CONSTRAINT fk_payment_requests_purchase_order_id
        FOREIGN KEY (purchase_order_id)
        REFERENCES purchase_orders(id)
        ON DELETE SET NULL;
        """
    )


def add_index(cursor) -> None:
    log("Creating index idx_payment_requests_purchase_order_id...")
    cursor.execute(
        """
        CREATE INDEX idx_payment_requests_purchase_order_id
        ON payment_requests (purchase_order_id);
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
                if not table_exists(cursor, "payment_requests"):
                    log("payment_requests table is missing; cannot apply migration.")
                    sys.exit(1)

                if not column_exists(cursor, "payment_requests", "purchase_order_id"):
                    add_column(cursor)
                else:
                    log("Column payment_requests.purchase_order_id already exists; skipping.")

                if not table_exists(cursor, "purchase_orders"):
                    log("purchase_orders table is missing; skipping FK constraint creation.")
                elif not constraint_exists(cursor, "fk_payment_requests_purchase_order_id"):
                    add_fk_constraint(cursor)
                else:
                    log("Constraint fk_payment_requests_purchase_order_id already exists; skipping.")

                if not index_exists(cursor, "idx_payment_requests_purchase_order_id"):
                    add_index(cursor)
                else:
                    log("Index idx_payment_requests_purchase_order_id already exists; skipping.")

                log("Migration completed successfully.")
    except psycopg2.Error as exc:
        log(f"Migration failed: {exc.pgerror or exc}")
        sys.exit(1)
    except Exception as exc:  # pragma: no cover - defensive
        log(f"Unexpected migration failure: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
