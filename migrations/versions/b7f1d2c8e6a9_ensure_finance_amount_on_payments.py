"""Ensure finance_amount column exists on payment_requests.

Revision ID: b7f1d2c8e6a9
Revises: a4b2c1d9f0ab
Create Date: 2025-02-15 00:00:01.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


# revision identifiers, used by Alembic.
revision = "b7f1d2c8e6a9"
down_revision = "a4b2c1d9f0ab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("payment_requests")}

    if "finance_amount" not in columns:
        with op.batch_alter_table("payment_requests") as batch_op:
            batch_op.add_column(sa.Column("finance_amount", sa.Numeric(14, 2), nullable=True))
        columns.add("finance_amount")

    if "amount_finance" in columns and "finance_amount" in columns:
        op.execute(
            text(
                "UPDATE payment_requests "
                "SET finance_amount = amount_finance "
                "WHERE finance_amount IS NULL"
            )
        )


def downgrade() -> None:
    # Non-destructive downgrade: keep finance_amount column if it exists.
    pass
