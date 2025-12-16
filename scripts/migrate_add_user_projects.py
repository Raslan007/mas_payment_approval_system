import os
import sys

from sqlalchemy import create_engine, text, inspect


LOG_PREFIX = "[user_projects migration]"


def log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}")
    sys.stdout.flush()


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return url


def ensure_table_exists(engine) -> None:
    inspector = inspect(engine)
    if inspector.has_table("user_projects"):
        log("Table user_projects already exists; skipping creation.")
        return

    log("Creating user_projects table...")
    create_sql = text(
        """
        CREATE TABLE user_projects (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, project_id)
        );
        """
    )
    with engine.begin() as conn:
        conn.execute(create_sql)
    log("Table created.")


def backfill_from_users(engine) -> None:
    log("Backfilling associations from existing users.project_id values...")
    insert_sql = text(
        """
        INSERT INTO user_projects (user_id, project_id)
        SELECT id AS user_id, project_id
        FROM users
        WHERE project_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM user_projects up
              WHERE up.user_id = users.id AND up.project_id = users.project_id
          );
        """
    )
    with engine.begin() as conn:
        result = conn.execute(insert_sql)
        log(f"Inserted {result.rowcount or 0} new association(s).")


def main() -> None:
    try:
        database_url = get_database_url()
    except RuntimeError as exc:
        log(f"Migration aborted: {exc}")
        sys.exit(1)

    engine = create_engine(database_url)

    try:
        ensure_table_exists(engine)
        backfill_from_users(engine)
        log("Migration completed successfully.")
    except Exception as exc:  # pragma: no cover - defensive
        log(f"Migration failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
