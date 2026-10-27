"""Explicit cross-utility asset dependency edge (Phase 16.0 foundation).

Edges are seed/configuration data — not inferred from zone, type, or profile ratings.
Impact propagation is intentionally out of scope for Phase 16.0.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.domain.enums import DependencyType

if TYPE_CHECKING:
    from app.domain.models.asset import Asset


class AssetDependency(Base):
    """
    Directed dependency: source_asset supplies/affects target_asset.

    Example (POC): FDR-A-01 --supplies--> PMP-A-01 means the pump depends on the feeder.
    """

    __tablename__ = "asset_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "source_asset_id",
            "target_asset_id",
            "dependency_type",
            name="uq_asset_dependencies_edge",
        ),
        CheckConstraint(
            "source_asset_id <> target_asset_id",
            name="ck_asset_dependencies_no_self",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_asset_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_asset_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dependency_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=DependencyType.SUPPLIES.value,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    source_asset: Mapped[Asset] = relationship(
        "Asset",
        foreign_keys=[source_asset_id],
    )
    target_asset: Mapped[Asset] = relationship(
        "Asset",
        foreign_keys=[target_asset_id],
    )
