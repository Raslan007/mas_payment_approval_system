"""Add PO allocation fields and link payment requests.

Revision ID: e7c9a1b4d2f3
Revises: d2e5f8a9c1d3
Create Date: 2025-02-16 00:00:02.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e7c9a1b4d2f3"
down_revision = "d2e5f8a9c1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "purchase_orders",
        sa.Column(
            "reserved_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "purchase_orders",
        sa.Column(
            "paid_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        """
        UPDATE purchase_orders
        SET remaining_amount = COALESCE(total_amount, 0) - COALESCE(advance_amount, 0)
        """
    )
    op.add_column(
        "payment_requests",
        sa.Column(
            "purchase_order_id",
            sa.Integer(),
            sa.ForeignKey("purchase_orders.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_payment_requests_purchase_order_id",
        "payment_requests",
        ["purchase_order_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payment_requests_purchase_order_id",
        table_name="payment_requests",
    )
    op.drop_column("payment_requests", "purchase_order_id")
    op.drop_column("purchase_orders", "paid_amount")
    op.drop_column("purchase_orders", "reserved_amount")
