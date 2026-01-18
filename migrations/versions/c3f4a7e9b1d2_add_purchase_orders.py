"""Add purchase orders table.

Revision ID: c3f4a7e9b1d2
Revises: a4b2c1d9f0ab
Create Date: 2025-02-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c3f4a7e9b1d2"
down_revision = "a4b2c1d9f0ab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bo_number", sa.String(length=50), nullable=False),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column("supplier_name", sa.String(length=255), nullable=False),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "advance_amount",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("remaining_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_purchase_orders_project_id",
        "purchase_orders",
        ["project_id"],
    )
    op.create_index(
        "ix_purchase_orders_created_by_id",
        "purchase_orders",
        ["created_by_id"],
    )
    op.create_index(
        "uq_purchase_orders_bo_number_ci",
        "purchase_orders",
        [sa.text("lower(bo_number)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_purchase_orders_bo_number_ci", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_created_by_id", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_project_id", table_name="purchase_orders")
    op.drop_table("purchase_orders")
