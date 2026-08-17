"""Add generic latitude/longitude to assets.

Revision ID: 0002_asset_geo
Revises: 0001_core_domain
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_asset_geo"
down_revision: str | None = "0001_core_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("assets", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("assets", "longitude")
    op.drop_column("assets", "latitude")
