import logging
import os

from flask import current_app
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from extensions import db

logger = logging.getLogger(__name__)
STARTUP_ADVISORY_LOCK_ID = 74290315


def run_startup_migrations() -> None:
    """Apply Alembic migrations at startup in a safe, non-blocking manner."""
    migrations_env = os.path.join(current_app.root_path, "migrations", "env.py")
    if not os.path.exists(migrations_env):
        current_app.logger.info(
            "Skipping DB migration auto-upgrade; migrations/env.py not found."
        )
        return

    from flask_migrate import upgrade

    try:
        upgrade()
        current_app.logger.info("DB migrations applied at startup")
    except Exception as exc:
        current_app.logger.exception(
            "DB migration auto-upgrade failed at startup",
            exc_info=exc,
        )


def _column_exists(table: str, column: str) -> bool:
    return bool(
        db.session.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = ANY(current_schemas(false))
                  AND table_name = :table
                  AND column_name = :column
                LIMIT 1
                """
            ),
            {"table": table, "column": column},
        ).scalar()
    )


def _table_exists(table: str) -> bool:
    return bool(
        db.session.execute(
            text(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = ANY(current_schemas(false))
                  AND table_name = :table
                LIMIT 1
                """
            ),
            {"table": table},
        ).scalar()
    )


def ensure_finance_amount_column() -> None:
    """Ensure payment_requests.finance_amount exists and is backfilled when possible."""
    if db.engine.dialect.name != "postgresql":
        current_app.logger.info(
            "Skipping finance_amount patch; unsupported dialect '%s'.",
            db.engine.dialect.name,
        )
        return

    try:
        if not _table_exists("payment_requests"):
            current_app.logger.error("payment_requests table missing; schema incompatible.")
            raise RuntimeError("payment_requests table missing; schema incompatible")

        if _column_exists("payment_requests", "finance_amount"):
            current_app.logger.info(
                "payment_requests.finance_amount column already present; no patch needed."
            )
            return

        db.session.execute(
            text(
                """
                ALTER TABLE payment_requests
                ADD COLUMN IF NOT EXISTS finance_amount NUMERIC(14,2)
                """
            )
        )

        if _column_exists("payment_requests", "amount_finance"):
            db.session.execute(
                text(
                    """
                    UPDATE payment_requests
                    SET finance_amount = amount_finance
                    WHERE finance_amount IS NULL
                    """
                )
            )
            current_app.logger.info(
                "Backfilled payment_requests.finance_amount from amount_finance."
            )

        db.session.commit()
        current_app.logger.info("Added payment_requests.finance_amount column.")
    except SQLAlchemyError as exc:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to apply finance_amount patch; schema may be incompatible.",
            exc_info=exc,
        )
        raise RuntimeError(
            "Schema incompatible; unable to ensure payment_requests.finance_amount"
        ) from exc


def run_startup_tasks() -> None:
    if os.getenv("RUN_STARTUP_MIGRATIONS") != "1":
        current_app.logger.info(
            "Skipping startup tasks; RUN_STARTUP_MIGRATIONS is not set to '1'."
        )
        return

    if db.engine.dialect.name != "postgresql":
        current_app.logger.info(
            "Skipping advisory lock; unsupported dialect '%s'.",
            db.engine.dialect.name,
        )
        ensure_finance_amount_column()
        run_startup_migrations()
        return

    current_app.logger.info("Acquiring startup advisory lock.")
    db.session.execute(
        text("SELECT pg_advisory_lock(:lock_id)"),
        {"lock_id": STARTUP_ADVISORY_LOCK_ID},
    )
    try:
        ensure_finance_amount_column()
        run_startup_migrations()
    finally:
        db.session.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": STARTUP_ADVISORY_LOCK_ID},
        )
        current_app.logger.info("Released startup advisory lock.")
