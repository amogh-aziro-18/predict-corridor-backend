"""Generic field technician — single registry with utility domain specialization."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.domain.enums import TechnicianAvailability

if TYPE_CHECKING:
    from app.domain.models.work_order import WorkOrder


class Technician(Base):
    """
    Operational technician registry.

    ``utility_kinds`` lists domains this technician may be assigned to
    (e.g. ``["water"]``, ``["power"]``, or ``["water", "power"]``).
    """

    __tablename__ = "technicians"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(128), nullable=False)
    primary_zone_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    skills: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    utility_kinds: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default='["water"]'
    )
    availability: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TechnicianAvailability.AVAILABLE.value
    )
    emergency_certified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    assigned_work_orders: Mapped[list[WorkOrder]] = relationship(
        "WorkOrder", back_populates="assigned_technician"
    )
