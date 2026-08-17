"""Utility registration row — one kind per corridor (e.g. water, power)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.domain.models.corridor import Corridor


class Utility(Base):
    """
    Persistent registry of utilities enabled for a corridor.

    Domain behaviour lives in `app.utilities` plugins; this table stores
    identity and enablement only.
    """

    __tablename__ = "utilities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    corridor_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("corridors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    corridor: Mapped[Corridor] = relationship("Corridor", back_populates="utilities")
