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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("purchase_orders"):
        return
    columns = {column["name"] for column in inspector.get_columns("purchase_orders")}
    indexes = {index["name"] for index in inspector.get_indexes("purchase_orders")}
    foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("purchase_orders")}

    if "deleted_at" not in columns:
        op.add_column(
            "purchase_orders",
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
        )
    if "deleted_by_id" not in columns:
        op.add_column(
            "purchase_orders",
            sa.Column("deleted_by_id", sa.Integer(), nullable=True),
        )
    if "ix_purchase_orders_deleted_at" not in indexes:
        op.create_index(
            "ix_purchase_orders_deleted_at",
            "purchase_orders",
            ["deleted_at"],
        )
    if "ix_purchase_orders_deleted_by_id" not in indexes:
        op.create_index(
            "ix_purchase_orders_deleted_by_id",
            "purchase_orders",
            ["deleted_by_id"],
        )
    if "fk_purchase_orders_deleted_by_id_users" not in foreign_keys:
        op.create_foreign_key(
            "fk_purchase_orders_deleted_by_id_users",
            "purchase_orders",
            "users",
            ["deleted_by_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("purchase_orders"):
        return
    columns = {column["name"] for column in inspector.get_columns("purchase_orders")}
    indexes = {index["name"] for index in inspector.get_indexes("purchase_orders")}
    foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("purchase_orders")}

    if "fk_purchase_orders_deleted_by_id_users" in foreign_keys:
        op.drop_constraint(
            "fk_purchase_orders_deleted_by_id_users",
            "purchase_orders",
            type_="foreignkey",
        )
    if "ix_purchase_orders_deleted_by_id" in indexes:
        op.drop_index(
            "ix_purchase_orders_deleted_by_id",
            table_name="purchase_orders",
        )
    if "ix_purchase_orders_deleted_at" in indexes:
        op.drop_index(
            "ix_purchase_orders_deleted_at",
            table_name="purchase_orders",
        )
    if "deleted_by_id" in columns:
        op.drop_column("purchase_orders", "deleted_by_id")
    if "deleted_at" in columns:
        op.drop_column("purchase_orders", "deleted_at")
