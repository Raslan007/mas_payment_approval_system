"""Create payment finance adjustments table.

Revision ID: a4b2c1d9f0ab
Revises: b7f1d2c8e6a9
Create Date: 2025-02-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a4b2c1d9f0ab"
down_revision = "b7f1d2c8e6a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "payment_finance_adjustments" in inspector.get_table_names():
        return

    op.create_table(
        "payment_finance_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("payment_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delta_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("is_void", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "voided_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("voided_at", sa.DateTime(), nullable=True),
        sa.Column("void_reason", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_payment_finance_adjustments_payment_id",
        "payment_finance_adjustments",
        ["payment_id"],
    )
    op.create_index(
        "ix_payment_finance_adjustments_created_by_user_id",
        "payment_finance_adjustments",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_payment_finance_adjustments_created_at",
        "payment_finance_adjustments",
        ["created_at"],
    )
    op.create_index(
        "ix_payment_finance_adjustments_is_void",
        "payment_finance_adjustments",
        ["is_void"],
    )
    op.create_index(
        "ix_payment_finance_adjustments_voided_by_user_id",
        "payment_finance_adjustments",
        ["voided_by_user_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "payment_finance_adjustments" not in inspector.get_table_names():
        return

    indexes = {
        index["name"] for index in inspector.get_indexes("payment_finance_adjustments")
    }
    if "ix_payment_finance_adjustments_voided_by_user_id" in indexes:
        op.drop_index(
            "ix_payment_finance_adjustments_voided_by_user_id",
            table_name="payment_finance_adjustments",
        )
    if "ix_payment_finance_adjustments_is_void" in indexes:
        op.drop_index(
            "ix_payment_finance_adjustments_is_void",
            table_name="payment_finance_adjustments",
        )
    if "ix_payment_finance_adjustments_created_at" in indexes:
        op.drop_index(
            "ix_payment_finance_adjustments_created_at",
            table_name="payment_finance_adjustments",
        )
    if "ix_payment_finance_adjustments_created_by_user_id" in indexes:
        op.drop_index(
            "ix_payment_finance_adjustments_created_by_user_id",
            table_name="payment_finance_adjustments",
        )
    if "ix_payment_finance_adjustments_payment_id" in indexes:
        op.drop_index(
            "ix_payment_finance_adjustments_payment_id",
            table_name="payment_finance_adjustments",
        )
    op.drop_table("payment_finance_adjustments")
