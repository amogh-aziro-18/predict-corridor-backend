"""Persisted operational scenario state (shared by API and simulator)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class OperationalScenarioState(Base):
    """
    Single-row scenario state for the corridor water utility.

    ``scenario_key`` / ``intensity`` remain the corridor-wide defaults.
    ``zone_scenarios`` optionally overrides per zone::

        {
          "zone-c": {"scenario_key": "pump_failure", "intensity": 0.4},
          "zone-d": {"scenario_key": "power_outage", "intensity": 0.7}
        }

    Empty map → existing global-only behaviour (backward compatible).
    """

    __tablename__ = "operational_scenario_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="current")
    scenario_key: Mapped[str] = mapped_column(
        String(64), nullable=False, default="normal_operations"
    )
    previous_scenario_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intensity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    zone_scenarios: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
