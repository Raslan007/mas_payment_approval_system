"""Add proxy role marker to purchase order decisions.

Revision ID: c6d7e8f9a0b1
Revises: 9c8b7a6d5e4f
Create Date: 2025-02-16 00:00:04.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c6d7e8f9a0b1"
down_revision = "9c8b7a6d5e4f"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("purchase_order_decisions"):
        return

    if not _has_column(inspector, "purchase_order_decisions", "proxy_for_role"):
        op.add_column(
            "purchase_order_decisions",
            sa.Column("proxy_for_role", sa.String(length=50), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("purchase_order_decisions"):
        return

    if _has_column(inspector, "purchase_order_decisions", "proxy_for_role"):
        op.drop_column("purchase_order_decisions", "proxy_for_role")
