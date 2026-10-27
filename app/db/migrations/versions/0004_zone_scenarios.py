"""Add per-zone scenario overrides on operational_scenario_state.

Revision ID: 0004_zone_scenarios
Revises: 0003_operational_scenario
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004_zone_scenarios"
down_revision: str | None = "0003_operational_scenario"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operational_scenario_state",
        sa.Column(
            "zone_scenarios",
            JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("operational_scenario_state", "zone_scenarios")
