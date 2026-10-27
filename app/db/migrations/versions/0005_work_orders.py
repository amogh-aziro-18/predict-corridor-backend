"""Work orders, technicians, and audit events.

Revision ID: 0005_work_orders
Revises: 0004_zone_scenarios
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_work_orders"
down_revision: str | None = "0004_zone_scenarios"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "technicians",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=128), nullable=False),
        sa.Column("primary_zone_id", sa.String(length=64), nullable=True),
        sa.Column(
            "skills",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("availability", sa.String(length=32), nullable=False, server_default="available"),
        sa.Column(
            "emergency_certified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
        sa.ForeignKeyConstraint(["primary_zone_id"], ["zones.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_technicians_primary_zone_id", "technicians", ["primary_zone_id"])

    op.create_table(
        "work_orders",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("utility", sa.String(length=64), nullable=False),
        sa.Column("zone_id", sa.String(length=64), nullable=False),
        sa.Column("recommendation_id", sa.String(length=128), nullable=True),
        sa.Column("recommendation_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(length=32), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("assigned_technician_id", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_notes", sa.Text(), nullable=True),
        sa.Column("verification_notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_technician_id"], ["technicians.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_work_orders_asset_id", "work_orders", ["asset_id"])
    op.create_index("ix_work_orders_utility", "work_orders", ["utility"])
    op.create_index("ix_work_orders_zone_id", "work_orders", ["zone_id"])
    op.create_index("ix_work_orders_status", "work_orders", ["status"])
    op.create_index("ix_work_orders_recommendation_id", "work_orders", ["recommendation_id"])
    op.create_index("ix_work_orders_assigned_technician_id", "work_orders", ["assigned_technician_id"])

    op.create_table(
        "work_order_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("work_order_id", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_work_order_events_work_order_id", "work_order_events", ["work_order_id"])


def downgrade() -> None:
    op.drop_index("ix_work_order_events_work_order_id", table_name="work_order_events")
    op.drop_table("work_order_events")
    op.drop_index("ix_work_orders_assigned_technician_id", table_name="work_orders")
    op.drop_index("ix_work_orders_recommendation_id", table_name="work_orders")
    op.drop_index("ix_work_orders_status", table_name="work_orders")
    op.drop_index("ix_work_orders_zone_id", table_name="work_orders")
    op.drop_index("ix_work_orders_utility", table_name="work_orders")
    op.drop_index("ix_work_orders_asset_id", table_name="work_orders")
    op.drop_table("work_orders")
    op.drop_index("ix_technicians_primary_zone_id", table_name="technicians")
    op.drop_table("technicians")
