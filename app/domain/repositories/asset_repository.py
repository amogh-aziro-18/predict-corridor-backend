"""Asset repository."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.models import Asset


class AssetRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, asset_id: str) -> Asset | None:
        return self._db.get(Asset, asset_id)

    def list(
        self,
        *,
        utility_kind: str | None = None,
        zone_id: str | None = None,
        corridor_id: str | None = None,
        asset_type: str | None = None,
    ) -> list[Asset]:
        stmt = select(Asset).order_by(Asset.id)
        if utility_kind:
            stmt = stmt.where(Asset.utility_kind == utility_kind)
        if zone_id:
            stmt = stmt.where(Asset.zone_id == zone_id)
        if corridor_id:
            stmt = stmt.where(Asset.corridor_id == corridor_id)
        if asset_type:
            stmt = stmt.where(Asset.type == asset_type)
        return list(self._db.scalars(stmt).all())

    def count(
        self,
        *,
        utility_kind: str | None = None,
        zone_id: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(Asset)
        if utility_kind:
            stmt = stmt.where(Asset.utility_kind == utility_kind)
        if zone_id:
            stmt = stmt.where(Asset.zone_id == zone_id)
        return int(self._db.scalar(stmt) or 0)

    def average_health(self, *, utility_kind: str | None = None) -> float | None:
        stmt = select(func.avg(Asset.health))
        if utility_kind:
            stmt = stmt.where(Asset.utility_kind == utility_kind)
        value = self._db.scalar(stmt)
        return float(value) if value is not None else None

    def upsert(self, asset: Asset) -> Asset:
        merged = self._db.merge(asset)
        self._db.flush()
        return merged
