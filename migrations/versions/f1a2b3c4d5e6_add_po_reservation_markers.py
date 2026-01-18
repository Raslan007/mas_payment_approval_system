"""Add purchase order reservation markers to payment requests.

Revision ID: f1a2b3c4d5e6
Revises: e7c9a1b4d2f3
Create Date: 2025-02-16 00:00:03.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "e7c9a1b4d2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_column(table: str, column: str) -> bool:
        return column in {col["name"] for col in inspector.get_columns(table)}

    def has_index(table: str, index_name: str) -> bool:
        return index_name in {idx["name"] for idx in inspector.get_indexes(table)}

    def has_fk(table: str, fk_name: str) -> bool:
        return fk_name in {fk["name"] for fk in inspector.get_foreign_keys(table)}

    if has_table("payment_requests") and not has_column(
        "payment_requests", "purchase_order_reserved_at"
    ):
        op.add_column(
            "payment_requests",
            sa.Column("purchase_order_reserved_at", sa.DateTime(), nullable=True),
        )
    if has_table("payment_requests") and not has_column(
        "payment_requests", "purchase_order_reserved_amount"
    ):
        op.add_column(
            "payment_requests",
            sa.Column(
                "purchase_order_reserved_amount", sa.Numeric(14, 2), nullable=True
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(name: str) -> bool:
        return inspector.has_table(name)

    def has_column(table: str, column: str) -> bool:
        return column in {col["name"] for col in inspector.get_columns(table)}

    def has_index(table: str, index_name: str) -> bool:
        return index_name in {idx["name"] for idx in inspector.get_indexes(table)}

    def has_fk(table: str, fk_name: str) -> bool:
        return fk_name in {fk["name"] for fk in inspector.get_foreign_keys(table)}

    if has_table("payment_requests") and has_column(
        "payment_requests", "purchase_order_reserved_amount"
    ):
        op.drop_column("payment_requests", "purchase_order_reserved_amount")
    if has_table("payment_requests") and has_column(
        "payment_requests", "purchase_order_reserved_at"
    ):
        op.drop_column("payment_requests", "purchase_order_reserved_at")
