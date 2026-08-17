"""Operational scenario state table.

Revision ID: 0003_operational_scenario
Revises: 0002_asset_geo
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_operational_scenario"
down_revision: str | None = "0002_asset_geo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_scenario_state",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("scenario_key", sa.String(length=64), nullable=False),
        sa.Column("previous_scenario_key", sa.String(length=64), nullable=True),
        sa.Column("intensity", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "selected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO operational_scenario_state (id, scenario_key, intensity)
            VALUES ('current', 'normal_operations', 1.0)
            """
        )
    )


def downgrade() -> None:
    op.drop_table("operational_scenario_state")
