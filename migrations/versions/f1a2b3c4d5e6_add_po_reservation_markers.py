"""Add purchase order reservation markers to payment requests.

Revision ID: f1a2b3c4d5e6
Revises: e7c9a1b4d2f3
Create Date: 2025-02-16 00:00:03.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "e7c9a1b4d2f3"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("payment_requests"):
        return

    if not _has_column(inspector, "payment_requests", "purchase_order_reserved_at"):
        op.add_column(
            "payment_requests",
            sa.Column("purchase_order_reserved_at", sa.DateTime(), nullable=True),
        )

    if not _has_column(
        inspector, "payment_requests", "purchase_order_reserved_amount"
    ):
        op.add_column(
            "payment_requests",
            sa.Column(
                "purchase_order_reserved_amount", sa.Numeric(14, 2), nullable=True
            ),
        )

    if not _has_column(inspector, "payment_requests", "purchase_order_finalized_at"):
        op.add_column(
            "payment_requests",
            sa.Column("purchase_order_finalized_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("payment_requests"):
        return

    if _has_column(inspector, "payment_requests", "purchase_order_finalized_at"):
        op.drop_column("payment_requests", "purchase_order_finalized_at")

    if _has_column(inspector, "payment_requests", "purchase_order_reserved_amount"):
        op.drop_column("payment_requests", "purchase_order_reserved_amount")

    if _has_column(inspector, "payment_requests", "purchase_order_reserved_at"):
        op.drop_column("payment_requests", "purchase_order_reserved_at")
