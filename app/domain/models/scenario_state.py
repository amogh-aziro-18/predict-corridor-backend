"""Persisted operational scenario state (shared by API and simulator)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class OperationalScenarioState(Base):
    """Single-row scenario state for the corridor water utility."""

    __tablename__ = "operational_scenario_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="current")
    scenario_key: Mapped[str] = mapped_column(String(64), nullable=False, default="normal_operations")
    previous_scenario_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intensity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
