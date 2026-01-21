"""Add users.project_id column if missing.

Revision ID: e1c2d3f4a5b6
Revises: f3a5ae35d85c
Create Date: 2026-01-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e1c2d3f4a5b6"
down_revision = "f3a5ae35d85c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("users"):
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("users")}

    if "project_id" not in columns:
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        columns.add("project_id")

    if "project_id" in columns and "fk_users_project_id_projects" not in foreign_keys:
        op.create_foreign_key(
            "fk_users_project_id_projects",
            "users",
            "projects",
            ["project_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("users"):
        return
    columns = {column["name"] for column in inspector.get_columns("users")}
    foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("users")}

    if "fk_users_project_id_projects" in foreign_keys:
        op.drop_constraint(
            "fk_users_project_id_projects",
            "users",
            type_="foreignkey",
        )
    if "project_id" in columns:
        op.drop_column("users", "project_id")
