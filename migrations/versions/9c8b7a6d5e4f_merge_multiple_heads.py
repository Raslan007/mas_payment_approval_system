"""merge multiple heads

Revision ID: 9c8b7a6d5e4f
Revises: 1a2b3c4d5e6f, e1c2d3f4a5b6
Create Date: 2026-01-21 14:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9c8b7a6d5e4f"
down_revision = ("1a2b3c4d5e6f", "e1c2d3f4a5b6")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
