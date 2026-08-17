"""Initial core domain schema.

Revision ID: 0001_core_domain
Revises:
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_core_domain"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "corridors",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("authority", sa.String(length=255), nullable=False),
        sa.Column("length_km", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "zones",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("corridor_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("area_sq_km", sa.Float(), nullable=False),
        sa.Column("health", sa.Float(), nullable=False),
        sa.Column("population", sa.Integer(), nullable=False),
        sa.Column("critical_assets", sa.Integer(), nullable=False),
        sa.Column(
            "utility_kinds",
            postgresql.ARRAY(sa.String(length=64)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["corridor_id"], ["corridors.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_zones_corridor_id", "zones", ["corridor_id"])

    op.create_table(
        "utilities",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("corridor_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["corridor_id"], ["corridors.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_utilities_corridor_id", "utilities", ["corridor_id"])
    op.create_index("ix_utilities_kind", "utilities", ["kind"])

    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("corridor_id", sa.String(length=64), nullable=False),
        sa.Column("zone_id", sa.String(length=64), nullable=False),
        sa.Column("utility_kind", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("health", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("risk", sa.String(length=32), nullable=False),
        sa.Column("weather_exposure", sa.String(length=32), nullable=False),
        sa.Column("flood_risk", sa.String(length=32), nullable=False),
        sa.Column("criticality", sa.String(length=32), nullable=False),
        sa.Column("backup", sa.String(length=32), nullable=False),
        sa.Column("recovery_priority", sa.Integer(), nullable=False),
        sa.Column("last_maintenance", sa.Date(), nullable=True),
        sa.Column("next_inspection", sa.Date(), nullable=True),
        sa.Column("predicted_failure_days", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["corridor_id"], ["corridors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_assets_corridor_id", "assets", ["corridor_id"])
    op.create_index("ix_assets_zone_id", "assets", ["zone_id"])
    op.create_index("ix_assets_utility_kind", "assets", ["utility_kind"])

    op.create_table(
        "sensors",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("metric_key", sa.String(length=128), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_sensors_asset_id", "sensors", ["asset_id"])
    op.create_index("ix_sensors_metric_key", "sensors", ["metric_key"])

    op.create_table(
        "telemetry",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("sensor_id", sa.String(length=64), nullable=False),
        sa.Column("metric_key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("quality", sa.String(length=32), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensors.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_telemetry_sensor_id", "telemetry", ["sensor_id"])
    op.create_index("ix_telemetry_metric_key", "telemetry", ["metric_key"])
    op.create_index("ix_telemetry_recorded_at", "telemetry", ["recorded_at"])


def downgrade() -> None:
    op.drop_table("telemetry")
    op.drop_table("sensors")
    op.drop_table("assets")
    op.drop_table("utilities")
    op.drop_table("zones")
    op.drop_table("corridors")
