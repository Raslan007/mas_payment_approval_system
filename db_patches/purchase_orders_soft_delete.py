from flask import current_app
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from extensions import db


def _table_exists(inspector, table: str) -> bool:
    return inspector.has_table(table)


def _column_names(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def ensure_purchase_orders_soft_delete_columns() -> None:
    """Ensure purchase_orders soft delete columns, FK, and index exist."""
    if db.engine.dialect.name != "postgresql":
        current_app.logger.info(
            "Skipping purchase_orders soft delete patch; unsupported dialect '%s'.",
            db.engine.dialect.name,
        )
        return

    current_app.logger.info("Starting purchase_orders soft delete patch.")
    try:
        with db.engine.begin() as connection:
            inspector = inspect(connection)

            if not _table_exists(inspector, "purchase_orders"):
                current_app.logger.error(
                    "purchase_orders table missing; schema incompatible."
                )
                return

            column_names = _column_names(inspector, "purchase_orders")

            if "deleted_at" not in column_names:
                current_app.logger.info(
                    "Adding purchase_orders.deleted_at column..."
                )
                connection.execute(
                    text(
                        """
                        ALTER TABLE purchase_orders
                        ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ
                        """
                    )
                )
                current_app.logger.info("Added purchase_orders.deleted_at column.")
            else:
                current_app.logger.info(
                    "purchase_orders.deleted_at column already present; skipping."
                )

            if "deleted_by_id" not in column_names:
                current_app.logger.info(
                    "Adding purchase_orders.deleted_by_id column..."
                )
                connection.execute(
                    text(
                        """
                        ALTER TABLE purchase_orders
                        ADD COLUMN IF NOT EXISTS deleted_by_id INTEGER
                        """
                    )
                )
                current_app.logger.info("Added purchase_orders.deleted_by_id column.")
            else:
                current_app.logger.info(
                    "purchase_orders.deleted_by_id column already present; skipping."
                )

            if _table_exists(inspector, "users"):
                connection.execute(
                    text(
                        """
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1
                                FROM pg_constraint
                                WHERE conname = 'fk_purchase_orders_deleted_by_id'
                            ) THEN
                                ALTER TABLE purchase_orders
                                ADD CONSTRAINT fk_purchase_orders_deleted_by_id
                                FOREIGN KEY (deleted_by_id) REFERENCES users(id);
                            END IF;
                        END $$;
                        """
                    )
                )
                current_app.logger.info(
                    "Ensured purchase_orders.deleted_by_id foreign key exists."
                )
            else:
                current_app.logger.error(
                    "users table missing; cannot enforce deleted_by_id foreign key."
                )

            connection.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_purchase_orders_deleted_at
                    ON purchase_orders (deleted_at)
                    """
                )
            )
            current_app.logger.info(
                "Ensured purchase_orders.deleted_at index exists."
            )
    except SQLAlchemyError as exc:
        current_app.logger.exception(
            "Failed to ensure purchase_orders soft delete columns.",
            exc_info=exc,
        )
