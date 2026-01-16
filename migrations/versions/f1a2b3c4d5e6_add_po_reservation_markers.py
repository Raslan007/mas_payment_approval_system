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


def upgrade() -> None:
    op.add_column(
        "payment_requests",
        sa.Column("purchase_order_reserved_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "payment_requests",
        sa.Column("purchase_order_reserved_amount", sa.Numeric(14, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_requests", "purchase_order_reserved_amount")
    op.drop_column("payment_requests", "purchase_order_reserved_at")
