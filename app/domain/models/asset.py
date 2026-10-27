"""Generic asset — utility-agnostic operational entity."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.domain.models.sensor import Sensor
    from app.domain.models.work_order import WorkOrder
    from app.domain.models.zone import Zone


class Asset(Base):
    """
    Shared asset projection matching the frontend Asset contract.

    Utility-specific attributes belong in `profile` (JSONB) and are validated
    by the owning utility plugin — never as columns on this table.
    """

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    corridor_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("corridors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("zones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    utility_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(128), nullable=False)

    # Generic geospatial fields (all utilities). Water-specific attrs stay in profile.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    health: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="operational")
    risk: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    # Static catalog metadata assigned at seed time — NOT live weather-derived risk.
    # See weather impact assessment (Phase 8C+) for dynamic environmental signals.
    weather_exposure: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    # Static catalog metadata for design-time / planning exposure — NOT live flood
    # probability and NOT updated from Open-Meteo or telemetry.
    flood_risk: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    criticality: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    backup: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    recovery_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    last_maintenance: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_inspection: Mapped[date | None] = mapped_column(Date, nullable=True)
    predicted_failure_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    zone: Mapped[Zone] = relationship("Zone", back_populates="assets")
    sensors: Mapped[list[Sensor]] = relationship("Sensor", back_populates="asset")
    work_orders: Mapped[list[WorkOrder]] = relationship("WorkOrder", back_populates="asset")
