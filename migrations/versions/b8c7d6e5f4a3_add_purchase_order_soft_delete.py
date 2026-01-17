"""Add soft delete fields to purchase orders.

Revision ID: b8c7d6e5f4a3
Revises: a1b2c3d4e5f6
Create Date: 2025-02-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b8c7d6e5f4a3"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_orders", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.add_column("purchase_orders", sa.Column("deleted_by_id", sa.Integer(), nullable=True))
    op.create_index("ix_purchase_orders_deleted_at", "purchase_orders", ["deleted_at"])
    op.create_index("ix_purchase_orders_deleted_by_id", "purchase_orders", ["deleted_by_id"])
    op.create_foreign_key(
        "fk_purchase_orders_deleted_by_id_users",
        "purchase_orders",
        "users",
        ["deleted_by_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_purchase_orders_deleted_by_id_users", "purchase_orders", type_="foreignkey")
    op.drop_index("ix_purchase_orders_deleted_by_id", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_deleted_at", table_name="purchase_orders")
    op.drop_column("purchase_orders", "deleted_by_id")
    op.drop_column("purchase_orders", "deleted_at")
