"""Add PO allocation fields and link payment requests.

Revision ID: e7c9a1b4d2f3
Revises: d2e5f8a9c1d3
Create Date: 2025-02-16 00:00:02.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision = "e7c9a1b4d2f3"
down_revision = "d2e5f8a9c1d3"
branch_labels = None
depends_on = None


def _get_inspector() -> Inspector:
    return sa.inspect(op.get_bind())


def has_table(name: str) -> bool:
    return _get_inspector().has_table(name)


def has_column(table: str, column: str) -> bool:
    inspector = _get_inspector()
    if not inspector.has_table(table):
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def has_index(table: str, index_name: str) -> bool:
    inspector = _get_inspector()
    if not inspector.has_table(table):
        return False
    return index_name in {idx["name"] for idx in inspector.get_indexes(table)}


def has_fk(table: str, fk_name: str) -> bool:
    inspector = _get_inspector()
    if not inspector.has_table(table):
        return False
    return fk_name in {fk["name"] for fk in inspector.get_foreign_keys(table)}


def upgrade() -> None:
    if has_table("purchase_orders") and not has_column(
        "purchase_orders", "reserved_amount"
    ):
        op.add_column(
            "purchase_orders",
            sa.Column(
                "reserved_amount",
                sa.Numeric(14, 2),
                nullable=False,
                server_default="0",
            ),
        )
    if has_table("purchase_orders") and not has_column("purchase_orders", "paid_amount"):
        op.add_column(
            "purchase_orders",
            sa.Column(
                "paid_amount",
                sa.Numeric(14, 2),
                nullable=False,
                server_default="0",
            ),
        )
    if has_table("purchase_orders"):
        op.execute(
            """
            UPDATE purchase_orders
            SET remaining_amount = COALESCE(total_amount, 0) - COALESCE(advance_amount, 0)
            """
        )
    if has_table("payment_requests") and not has_column(
        "payment_requests", "purchase_order_id"
    ):
        op.add_column(
            "payment_requests",
            sa.Column(
                "purchase_order_id",
                sa.Integer(),
                sa.ForeignKey("purchase_orders.id"),
                nullable=True,
            ),
        )
    if has_table("payment_requests") and not has_index(
        "payment_requests", "ix_payment_requests_purchase_order_id"
    ):
        op.create_index(
            "ix_payment_requests_purchase_order_id",
            "payment_requests",
            ["purchase_order_id"],
        )


def downgrade() -> None:
    if has_index("payment_requests", "ix_payment_requests_purchase_order_id"):
        op.drop_index(
            "ix_payment_requests_purchase_order_id",
            table_name="payment_requests",
        )
    if has_column("payment_requests", "purchase_order_id"):
        op.drop_column("payment_requests", "purchase_order_id")
    if has_column("purchase_orders", "paid_amount"):
        op.drop_column("purchase_orders", "paid_amount")
    if has_column("purchase_orders", "reserved_amount"):
        op.drop_column("purchase_orders", "reserved_amount")
