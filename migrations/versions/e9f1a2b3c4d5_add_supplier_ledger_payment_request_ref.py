"""Add payment_request_id reference to supplier ledger entries.

Revision ID: e9f1a2b3c4d5
Revises: d8a9b0c1d2e3
Create Date: 2026-02-11 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e9f1a2b3c4d5"
down_revision = "d8a9b0c1d2e3"
branch_labels = None
depends_on = None


TABLE_NAME = "supplier_ledger_entries"


def _constraint_exists(inspector, table_name: str, constraint_name: str) -> bool:
    return any(
        uc.get("name") == constraint_name
        for uc in inspector.get_unique_constraints(table_name)
    )


def _index_exists(inspector, table_name: str, index_name: str) -> bool:
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if TABLE_NAME not in inspector.get_table_names():
        return

    column_names = {column["name"] for column in inspector.get_columns(TABLE_NAME)}

    with op.batch_alter_table(TABLE_NAME) as batch_op:
        if "payment_request_id" not in column_names:
            batch_op.add_column(sa.Column("payment_request_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_supplier_ledger_entries_payment_request_id",
                "payment_requests",
                ["payment_request_id"],
                ["id"],
                ondelete="SET NULL",
            )

    inspector = sa.inspect(bind)
    if not _index_exists(inspector, TABLE_NAME, "ix_supplier_ledger_entries_payment_request_id"):
        op.create_index(
            "ix_supplier_ledger_entries_payment_request_id",
            TABLE_NAME,
            ["payment_request_id"],
            unique=False,
        )

    if not _constraint_exists(inspector, TABLE_NAME, "uq_supplier_ledger_entries_payment_request_id"):
        with op.batch_alter_table(TABLE_NAME) as batch_op:
            batch_op.create_unique_constraint(
                "uq_supplier_ledger_entries_payment_request_id",
                ["payment_request_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if TABLE_NAME not in inspector.get_table_names():
        return

    if _constraint_exists(inspector, TABLE_NAME, "uq_supplier_ledger_entries_payment_request_id"):
        with op.batch_alter_table(TABLE_NAME) as batch_op:
            batch_op.drop_constraint(
                "uq_supplier_ledger_entries_payment_request_id",
                type_="unique",
            )

    inspector = sa.inspect(bind)
    if _index_exists(inspector, TABLE_NAME, "ix_supplier_ledger_entries_payment_request_id"):
        op.drop_index("ix_supplier_ledger_entries_payment_request_id", table_name=TABLE_NAME)

    inspector = sa.inspect(bind)
    column_names = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    if "payment_request_id" in column_names:
        with op.batch_alter_table(TABLE_NAME) as batch_op:
            batch_op.drop_constraint(
                "fk_supplier_ledger_entries_payment_request_id",
                type_="foreignkey",
            )
            batch_op.drop_column("payment_request_id")
