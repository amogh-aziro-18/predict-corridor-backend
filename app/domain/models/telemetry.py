"""Time-stamped telemetry samples for a sensor."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.domain.models.sensor import Sensor


class Telemetry(Base):
    """
    Generic measurement row.

    `metric_key` is denormalized from the sensor for query convenience;
    interpretation is owned by the utility plugin catalog.
    """

    __tablename__ = "telemetry"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sensor_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    quality: Mapped[str] = mapped_column(String(32), nullable=False, default="good")
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sensor: Mapped[Sensor] = relationship("Sensor", back_populates="telemetry")
