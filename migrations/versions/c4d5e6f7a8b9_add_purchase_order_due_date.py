"""Add due date to purchase orders.

Revision ID: c4d5e6f7a8b9
Revises: a1b2c3d4e5f6
Create Date: 2025-02-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4d5e6f7a8b9"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("purchase_orders"):
        return
    columns = {column["name"] for column in inspector.get_columns("purchase_orders")}
    if "due_date" not in columns:
        op.add_column("purchase_orders", sa.Column("due_date", sa.Date(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("purchase_orders"):
        return
    columns = {column["name"] for column in inspector.get_columns("purchase_orders")}
    if "due_date" in columns:
        op.drop_column("purchase_orders", "due_date")
