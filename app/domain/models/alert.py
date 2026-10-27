"""Persistent operator attention alert — utility-agnostic condition record."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.domain.enums import AlertSource, AlertStatus

if TYPE_CHECKING:
    from app.domain.models.asset import Asset
    from app.domain.models.zone import Zone


class Alert(Base):
    """
    Durable operator attention record for a condition requiring awareness.

    Distinct from Recommendation (action draft), Incident (declared case),
    and notification delivery channels (toast/bell).
    """

    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AlertStatus.OPEN.value, index=True
    )
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default=AlertSource.TELEMETRY.value, index=True
    )
    alert_code: Mapped[str] = mapped_column(String(128), nullable=False)
    utility_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    zone_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("zones.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    asset_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recommendation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    raised_at: Mapped[datetime] = mapped_column(
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
    ack_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolve_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    zone: Mapped[Zone | None] = relationship("Zone")
    asset: Mapped[Asset | None] = relationship("Asset")
