"""indexes for state-based run queries (scheduler, tracking, duplicate check, channel owner lookup);
project_tracking.handoff_slack (internal #cs-handoffs post state: posted | pending | blocked)

Revision ID: e3a9c1f04b7d
Revises: d81f5a7e2b90
Create Date: 2026-11-12 12:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3a9c1f04b7d'
down_revision: str | Sequence[str] | None = 'd81f5a7e2b90'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("status", "opportunity_id", "slack_channel_id")


def upgrade() -> None:
    # Hand-written: autogenerate also proposes dropping LangGraph's checkpoint_* tables (not in our metadata).
    for col in COLUMNS:
        op.create_index(f"ix_onboarding_runs_{col}", "onboarding_runs", [col])
    op.add_column("project_tracking", sa.Column("handoff_slack", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_tracking", "handoff_slack")
    for col in COLUMNS:
        op.drop_index(f"ix_onboarding_runs_{col}", table_name="onboarding_runs")
