"""Geographic / operational zone within a corridor."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.domain.models.asset import Asset
    from app.domain.models.corridor import Corridor


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    corridor_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("corridors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    area_sq_km: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    health: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    population: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    critical_assets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    utility_kinds: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    corridor: Mapped[Corridor] = relationship("Corridor", back_populates="zones")
    assets: Mapped[list[Asset]] = relationship("Asset", back_populates="zone")
