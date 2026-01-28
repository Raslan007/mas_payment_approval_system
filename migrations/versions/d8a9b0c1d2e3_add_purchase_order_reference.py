"""Add purchase order reference and description.

Revision ID: d8a9b0c1d2e3
Revises: c6d7e8f9a0b1
Create Date: 2025-03-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d8a9b0c1d2e3"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "purchase_orders" not in inspector.get_table_names():
        return

    column_names = {column["name"] for column in inspector.get_columns("purchase_orders")}
    if "description" not in column_names:
        op.add_column("purchase_orders", sa.Column("description", sa.Text(), nullable=True))
    if "reference_po_number" not in column_names:
        op.add_column(
            "purchase_orders",
            sa.Column("reference_po_number", sa.String(length=50), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "purchase_orders" not in inspector.get_table_names():
        return

    column_names = {column["name"] for column in inspector.get_columns("purchase_orders")}
    if "reference_po_number" in column_names:
        op.drop_column("purchase_orders", "reference_po_number")
    if "description" in column_names:
        op.drop_column("purchase_orders", "description")
