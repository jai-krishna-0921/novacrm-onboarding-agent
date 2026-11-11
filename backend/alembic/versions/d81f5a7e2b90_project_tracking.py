"""project tracking (Agent 3): task snapshot + handoff record per run

Revision ID: d81f5a7e2b90
Revises: c4d2c13a630e
Create Date: 2026-11-11 23:59:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd81f5a7e2b90'
down_revision: str | Sequence[str] | None = 'c4d2c13a630e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Hand-written: autogenerate also proposes dropping LangGraph's checkpoint_* tables (not in our metadata).
    op.create_table('project_tracking',
    sa.Column('run_id', sa.String(), nullable=False),
    sa.Column('tasks', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('synced_at', sa.String(), nullable=True),
    sa.Column('handoff_summary', sa.Text(), nullable=True),
    sa.Column('handed_off_at', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('run_id')
    )


def downgrade() -> None:
    op.drop_table('project_tracking')
