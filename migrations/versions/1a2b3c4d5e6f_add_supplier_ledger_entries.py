"""add supplier ledger entries

Revision ID: 1a2b3c4d5e6f
Revises: f3a5ae35d85c
Create Date: 2026-01-18 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1a2b3c4d5e6f"
down_revision = "f3a5ae35d85c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "supplier_ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), nullable=False, index=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("entry_type", sa.String(length=30), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("voided_at", sa.DateTime(), nullable=True),
        sa.Column("voided_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["voided_by_id"], ["users.id"]),
        sa.CheckConstraint("amount > 0", name="ck_supplier_ledger_entries_amount_positive"),
        sa.CheckConstraint(
            "direction in ('debit','credit')",
            name="ck_supplier_ledger_entries_direction_valid",
        ),
        sa.CheckConstraint(
            "entry_type in ('opening_balance','adjustment')",
            name="ck_supplier_ledger_entries_entry_type_valid",
        ),
    )
    op.create_index(
        "ix_supplier_ledger_entries_supplier_date",
        "supplier_ledger_entries",
        ["supplier_id", "entry_date"],
    )
    op.create_index(
        "ix_supplier_ledger_entries_project_id",
        "supplier_ledger_entries",
        ["project_id"],
    )


def downgrade():
    op.drop_index(
        "ix_supplier_ledger_entries_supplier_date",
        table_name="supplier_ledger_entries",
    )
    op.drop_index(
        "ix_supplier_ledger_entries_project_id",
        table_name="supplier_ledger_entries",
    )
    op.drop_table("supplier_ledger_entries")
