"""Add purchase order decisions table.

Revision ID: d2e5f8a9c1d3
Revises: c3f4a7e9b1d2
Create Date: 2025-02-16 00:00:01.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d2e5f8a9c1d3"
down_revision = "c3f4a7e9b1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_order_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "purchase_order_id",
            sa.Integer(),
            sa.ForeignKey("purchase_orders.id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=False),
        sa.Column("to_status", sa.String(length=30), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "decided_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_purchase_order_decisions_purchase_order_id",
        "purchase_order_decisions",
        ["purchase_order_id"],
    )
    op.create_index(
        "ix_purchase_order_decisions_decided_by_id",
        "purchase_order_decisions",
        ["decided_by_id"],
    )
    op.create_index(
        "ix_purchase_order_decisions_decided_at",
        "purchase_order_decisions",
        ["decided_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_purchase_order_decisions_decided_at",
        table_name="purchase_order_decisions",
    )
    op.drop_index(
        "ix_purchase_order_decisions_decided_by_id",
        table_name="purchase_order_decisions",
    )
    op.drop_index(
        "ix_purchase_order_decisions_purchase_order_id",
        table_name="purchase_order_decisions",
    )
    op.drop_table("purchase_order_decisions")
