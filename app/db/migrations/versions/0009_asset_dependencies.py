"""Asset dependency edges for cross-utility foundation (Phase 16.0).

Revision ID: 0009_asset_dependencies
Revises: 0008_technician_utility_kinds
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_asset_dependencies"
down_revision: str | None = "0008_technician_utility_kinds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_dependencies",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("source_asset_id", sa.String(length=64), nullable=False),
        sa.Column("target_asset_id", sa.String(length=64), nullable=False),
        sa.Column(
            "dependency_type",
            sa.String(length=64),
            nullable=False,
            server_default="supplies",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
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
        sa.ForeignKeyConstraint(
            ["source_asset_id"], ["assets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_asset_id"], ["assets.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "source_asset_id",
            "target_asset_id",
            "dependency_type",
            name="uq_asset_dependencies_edge",
        ),
        sa.CheckConstraint(
            "source_asset_id <> target_asset_id",
            name="ck_asset_dependencies_no_self",
        ),
    )
    op.create_index(
        "ix_asset_dependencies_source_asset_id",
        "asset_dependencies",
        ["source_asset_id"],
    )
    op.create_index(
        "ix_asset_dependencies_target_asset_id",
        "asset_dependencies",
        ["target_asset_id"],
    )
    op.create_index(
        "ix_asset_dependencies_dependency_type",
        "asset_dependencies",
        ["dependency_type"],
    )
    op.create_index(
        "ix_asset_dependencies_enabled",
        "asset_dependencies",
        ["enabled"],
    )


def downgrade() -> None:
    op.drop_index("ix_asset_dependencies_enabled", table_name="asset_dependencies")
    op.drop_index("ix_asset_dependencies_dependency_type", table_name="asset_dependencies")
    op.drop_index("ix_asset_dependencies_target_asset_id", table_name="asset_dependencies")
    op.drop_index("ix_asset_dependencies_source_asset_id", table_name="asset_dependencies")
    op.drop_table("asset_dependencies")
