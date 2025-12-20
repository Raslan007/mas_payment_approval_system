"""Idempotent migration to add dashboard/filter indexes.

Adds indexes that speed up dashboards, list views, and saved filters:
- payment_requests(status, created_at DESC)
- payment_requests(project_id, status)
- payment_requests(submitted_to_pm_at)
- saved_views(user_id)

Safe to rerun on Postgres or SQLite (uses IF NOT EXISTS).
"""
import argparse
import os
import sys

from sqlalchemy import create_engine, inspect, text


LOG_PREFIX = "[add_indexes]"

INDEX_DEFINITIONS = [
    {
        "name": "ix_payment_requests_status_created_at",
        "table": "payment_requests",
        "columns": ["status", "created_at"],
        "expression": "status, created_at DESC",
    },
    {
        "name": "ix_payment_requests_project_status",
        "table": "payment_requests",
        "columns": ["project_id", "status"],
        "expression": "project_id, status",
    },
    {
        "name": "ix_payment_requests_submitted_to_pm_at",
        "table": "payment_requests",
        "columns": ["submitted_to_pm_at"],
        "expression": "submitted_to_pm_at",
    },
    {
        "name": "ix_saved_views_user_id",
        "table": "saved_views",
        "columns": ["user_id"],
        "expression": "user_id",
    },
]


def log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}")
    sys.stdout.flush()


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return url


def table_has_columns(inspector, table: str, columns: list[str]) -> bool:
    if not inspector.has_table(table):
        log(f"Table {table} is missing; skipping related indexes.")
        return False

    column_names = {col["name"] for col in inspector.get_columns(table)}
    missing = [col for col in columns if col not in column_names]
    if missing:
        log(
            f"Table {table} is missing columns {', '.join(missing)}; "
            f"skipping related indexes."
        )
        return False

    return True


def create_indexes(engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for index in INDEX_DEFINITIONS:
            if not table_has_columns(inspector, index["table"], index["columns"]):
                continue

            log(f"Ensuring index {index['name']} on {index['table']}...")
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {index['name']} "
                    f"ON {index['table']} ({index['expression']});"
                )
            )
    log("Index creation complete.")


def drop_indexes(engine) -> None:
    with engine.begin() as conn:
        for index in INDEX_DEFINITIONS:
            log(f"Dropping index {index['name']} if it exists...")
            conn.execute(text(f"DROP INDEX IF EXISTS {index['name']};"))
    log("Index drop complete.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add or drop idempotent indexes for dashboards/filters."
    )
    parser.add_argument(
        "--downgrade",
        action="store_true",
        help="Drop the indexes instead of creating them.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv or sys.argv[1:])

    try:
        database_url = get_database_url()
    except RuntimeError as exc:
        log(f"Migration aborted: {exc}")
        sys.exit(1)

    engine = create_engine(database_url)
    log(f"Connected using dialect {engine.dialect.name}.")

    try:
        if args.downgrade:
            drop_indexes(engine)
        else:
            create_indexes(engine)
        log("Migration completed successfully.")
    except Exception as exc:  # pragma: no cover - defensive
        log(f"Migration failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
