"""Asset service — generic asset queries across utilities."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.schemas.asset import AssetOut
from app.core.errors import NotFoundError
from app.domain.query import normalize_utility_kind, normalize_zone_id
from app.domain.repositories import AssetRepository
from app.services.mappers import asset_to_out


class AssetService:
    def __init__(self, db: Session) -> None:
        self._assets = AssetRepository(db)

    def list_assets(
        self,
        *,
        utility: str | None = None,
        zone: str | None = None,
        corridor_id: str | None = None,
        asset_type: str | None = None,
    ) -> list[AssetOut]:
        utility_kind = normalize_utility_kind(utility)
        zone_id = normalize_zone_id(zone)
        rows = self._assets.list(
            utility_kind=utility_kind,
            zone_id=zone_id,
            corridor_id=corridor_id,
            asset_type=asset_type.strip() if asset_type else None,
        )
        return [asset_to_out(a) for a in rows]

    def get_asset(self, asset_id: str) -> AssetOut:
        asset = self._assets.get(asset_id)
        if asset is None:
            raise NotFoundError(f"Asset not found: {asset_id}")
        return asset_to_out(asset)
