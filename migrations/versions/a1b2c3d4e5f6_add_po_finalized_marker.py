"""Add purchase order finalized marker to payment requests.

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2025-02-16 00:00:04.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_requests",
        sa.Column("purchase_order_finalized_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_requests", "purchase_order_finalized_at")
