"""Add purchase order advance amount column.

Revision ID: ab12cd34ef56
Revises: f1a2b3c4d5e6
Create Date: 2025-03-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ab12cd34ef56"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("purchase_orders"):
        return
    columns = {column["name"] for column in inspector.get_columns("purchase_orders")}
    if "advance_amount" not in columns:
        op.add_column(
            "purchase_orders",
            sa.Column(
                "advance_amount",
                sa.Numeric(14, 2),
                nullable=False,
                server_default="0",
            ),
        )
    op.execute(
        """
        UPDATE purchase_orders
        SET advance_amount = 0
        WHERE advance_amount IS NULL
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("purchase_orders"):
        return
    columns = {column["name"] for column in inspector.get_columns("purchase_orders")}
    if "advance_amount" in columns:
        op.drop_column("purchase_orders", "advance_amount")
