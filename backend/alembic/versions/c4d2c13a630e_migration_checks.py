"""migration checks

Revision ID: c4d2c13a630e
Revises: b7d3c04ba867
Create Date: 2026-11-11 23:30:21.582232

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d2c13a630e'
down_revision: str | Sequence[str] | None = 'b7d3c04ba867'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Hand-written: autogenerate also proposed dropping LangGraph's checkpoint_* tables (not in our metadata).
    op.create_table('migration_checks',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(), nullable=True),
    sa.Column('batch_id', sa.String(), nullable=True),
    sa.Column('ts', sa.String(), nullable=True),
    sa.Column('check_name', sa.String(), nullable=True),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_migration_checks_run_id'), 'migration_checks', ['run_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_migration_checks_run_id'), table_name='migration_checks')
    op.drop_table('migration_checks')
