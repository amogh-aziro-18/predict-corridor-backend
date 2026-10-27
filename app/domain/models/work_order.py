"""Generic operational work order — utility-agnostic execution record."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.domain.enums import WorkOrderSource, WorkOrderStatus

if TYPE_CHECKING:
    from app.domain.models.asset import Asset
    from app.domain.models.technician import Technician
    from app.domain.models.work_order_event import WorkOrderEvent


class WorkOrder(Base):
    """
    Persisted work order linked to canonical asset identity.

    ``utility`` and ``zone_id`` are denormalized from the asset for query/filter
  convenience; ``asset_id`` remains the source of truth.
    """

    __tablename__ = "work_orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    utility: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    zone_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    recommendation_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=WorkOrderStatus.OPEN.value, index=True
    )
    assigned_technician_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("technicians.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default=WorkOrderSource.MANUAL.value
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    asset: Mapped[Asset] = relationship("Asset", back_populates="work_orders")
    assigned_technician: Mapped[Technician | None] = relationship(
        "Technician", back_populates="assigned_work_orders"
    )
    events: Mapped[list[WorkOrderEvent]] = relationship(
        "WorkOrderEvent",
        back_populates="work_order",
        cascade="all, delete-orphan",
        order_by="WorkOrderEvent.created_at",
    )
