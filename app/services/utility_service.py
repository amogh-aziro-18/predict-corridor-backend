"""Utility service — generic summaries for enabled utilities."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.schemas.utility import UtilitySummaryOut
from app.domain.repositories import AssetRepository, UtilityRepository, ZoneRepository
from app.services.mappers import utility_to_summary
from app.utilities.registry import registry


class UtilityService:
    def __init__(self, db: Session) -> None:
        self._utilities = UtilityRepository(db)
        self._assets = AssetRepository(db)
        self._zones = ZoneRepository(db)

    def list_plugin_kinds(self) -> list[str]:
        return registry.kinds()

    def list_utilities(self, corridor_id: str | None = None) -> list[UtilitySummaryOut]:
        rows = self._utilities.list_enabled(corridor_id)
        out: list[UtilitySummaryOut] = []
        for utility in rows:
            zones_with_kind = [
                z.id
                for z in self._zones.list(corridor_id=utility.corridor_id)
                if utility.kind in (z.utility_kinds or [])
            ]
            asset_count = self._assets.count(utility_kind=utility.kind)
            avg_health = self._assets.average_health(utility_kind=utility.kind) or 90.0
            out.append(
                utility_to_summary(
                    utility,
                    zones=zones_with_kind,
                    asset_count=asset_count,
                    operational_health=avg_health,
                )
            )
        return out
