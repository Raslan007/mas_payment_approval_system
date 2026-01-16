"""Change payment request amount to Numeric.

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2025-02-16 00:00:05.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("payment_requests") as batch_op:
        batch_op.alter_column(
            "amount",
            existing_type=sa.Float(),
            type_=sa.Numeric(14, 2),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("payment_requests") as batch_op:
        batch_op.alter_column(
            "amount",
            existing_type=sa.Numeric(14, 2),
            type_=sa.Float(),
            existing_nullable=False,
        )
