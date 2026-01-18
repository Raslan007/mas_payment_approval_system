"""merge_heads

Revision ID: f3a5ae35d85c
Revises: ab12cd34ef56, b1c2d3e4f5a6, b8c7d6e5f4a3, c4d5e6f7a8b9
Create Date: 2026-01-18 16:04:14.125999

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a5ae35d85c'
down_revision = ('ab12cd34ef56', 'b1c2d3e4f5a6', 'b8c7d6e5f4a3', 'c4d5e6f7a8b9')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
