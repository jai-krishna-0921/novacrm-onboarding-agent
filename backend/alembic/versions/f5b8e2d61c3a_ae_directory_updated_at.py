"""ae_directory.updated_at (AE directory is edited from the dashboard)

Revision ID: f5b8e2d61c3a
Revises: e3a9c1f04b7d
Create Date: 2026-11-12 18:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f5b8e2d61c3a'
down_revision: str | Sequence[str] | None = 'e3a9c1f04b7d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Hand-written: autogenerate also proposes dropping LangGraph's checkpoint_* tables (not in our metadata).
    op.add_column("ae_directory", sa.Column("updated_at", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("ae_directory", "updated_at")
