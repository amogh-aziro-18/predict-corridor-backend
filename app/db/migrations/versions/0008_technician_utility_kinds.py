"""Add utility_kinds to technicians for domain specialization.

Revision ID: 0008_technician_utility_kinds
Revises: 0007_alerts
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_technician_utility_kinds"
down_revision: str | None = "0007_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "technicians",
        sa.Column(
            "utility_kinds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[\"water\"]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("technicians", "utility_kinds")
