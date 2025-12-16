"""Idempotent migration to add payment_requests.submitted_to_pm_at.

Steps:
- Add the nullable column if it doesn't exist.
- Backfill from the earliest engineer submit log (step='engineer', action in
  ('submit', 'submit_to_pm') or any log that moved the request to pending_pm).
- If no matching log exists, fall back to the payment's created_at.
- Safe to rerun and will log progress.
"""
import os
import sys
from contextlib import closing

import psycopg2


LOG_PREFIX = "[submitted_to_pm_at migration]"


def log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}")
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
        ("payment_requests", "submitted_to_pm_at"),
    )
    exists = cursor.fetchone()[0]
    return bool(exists)


def add_column(cursor) -> None:
    log("Adding submitted_to_pm_at column to payment_requests...")
    cursor.execute(
        """
        ALTER TABLE payment_requests
        ADD COLUMN submitted_to_pm_at TIMESTAMP WITHOUT TIME ZONE;
        """
    )


def backfill_column(cursor) -> None:
    log(
        "Backfilling submitted_to_pm_at from engineer submit logs or created_at when missing..."
    )
    cursor.execute(
        """
        WITH first_submit AS (
            SELECT
                payment_request_id,
                MIN(decided_at) AS first_submit_at
            FROM payment_approvals
            WHERE
                (step = 'engineer' AND action IN ('submit', 'submit_to_pm'))
                OR new_status = 'pending_pm'
            GROUP BY payment_request_id
        ),
        updated_with_logs AS (
            UPDATE payment_requests p
            SET submitted_to_pm_at = fs.first_submit_at
            FROM first_submit fs
            WHERE p.id = fs.payment_request_id
              AND p.submitted_to_pm_at IS NULL
            RETURNING p.id
        )
        UPDATE payment_requests p
        SET submitted_to_pm_at = p.created_at
        WHERE p.submitted_to_pm_at IS NULL;
        """
    )
    log(f"Updated {cursor.rowcount or 0} payment(s) with submission timestamps.")


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
                    log("Column payment_requests.submitted_to_pm_at already exists; skipping add.")
                else:
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
