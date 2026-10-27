"""Persistent operational incident — utility-agnostic declared event."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.domain.enums import IncidentSource, IncidentStatus

if TYPE_CHECKING:
    from app.domain.models.asset import Asset
    from app.domain.models.incident_event import IncidentEvent
    from app.domain.models.zone import Zone


class Incident(Base):
    """
    Stateful declared operational incident with auditable lifecycle.

    Distinct from ephemeral weather/environment/resilience assessments.
    """

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=IncidentStatus.OPEN.value, index=True
    )
    utility_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    zone_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("zones.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    asset_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("assets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default=IncidentSource.MANUAL.value
    )
    recommendation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    recommendation_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    declared_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    zone: Mapped[Zone | None] = relationship("Zone")
    asset: Mapped[Asset | None] = relationship("Asset")
    events: Mapped[list[IncidentEvent]] = relationship(
        "IncidentEvent",
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="IncidentEvent.created_at",
    )
