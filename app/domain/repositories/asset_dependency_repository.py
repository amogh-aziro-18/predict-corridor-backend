"""Asset dependency edge persistence (Phase 16.0)."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.domain.models import Asset, AssetDependency


class AssetDependencyRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, dependency_id: str) -> AssetDependency | None:
        return self._db.get(AssetDependency, dependency_id)

    def list(
        self,
        *,
        source_asset_id: str | None = None,
        target_asset_id: str | None = None,
        dependency_type: str | None = None,
        enabled: bool | None = True,
        utility_kind: str | None = None,
        zone_id: str | None = None,
    ) -> list[AssetDependency]:
        """
        List edges with optional filters.

        utility_kind / zone_id match if either endpoint asset matches.
        """
        stmt = (
            select(AssetDependency)
            .options(
                joinedload(AssetDependency.source_asset),
                joinedload(AssetDependency.target_asset),
            )
            .order_by(AssetDependency.id.asc())
        )
        if source_asset_id:
            stmt = stmt.where(AssetDependency.source_asset_id == source_asset_id)
        if target_asset_id:
            stmt = stmt.where(AssetDependency.target_asset_id == target_asset_id)
        if dependency_type:
            stmt = stmt.where(AssetDependency.dependency_type == dependency_type)
        if enabled is not None:
            stmt = stmt.where(AssetDependency.enabled.is_(enabled))

        if utility_kind or zone_id:
            source = Asset.__table__.alias("dep_source_asset")
            target = Asset.__table__.alias("dep_target_asset")
            stmt = stmt.join(source, AssetDependency.source_asset_id == source.c.id).join(
                target, AssetDependency.target_asset_id == target.c.id
            )
            if utility_kind:
                stmt = stmt.where(
                    or_(
                        source.c.utility_kind == utility_kind,
                        target.c.utility_kind == utility_kind,
                    )
                )
            if zone_id:
                stmt = stmt.where(
                    or_(source.c.zone_id == zone_id, target.c.zone_id == zone_id)
                )

        return list(self._db.scalars(stmt).unique().all())

    def list_from_source(
        self, source_asset_id: str, *, enabled_only: bool = True
    ) -> list[AssetDependency]:
        return self.list(
            source_asset_id=source_asset_id,
            enabled=True if enabled_only else None,
        )

    def list_to_target(
        self, target_asset_id: str, *, enabled_only: bool = True
    ) -> list[AssetDependency]:
        return self.list(
            target_asset_id=target_asset_id,
            enabled=True if enabled_only else None,
        )

    def list_involving_asset(
        self, asset_id: str, *, enabled_only: bool = True
    ) -> list[AssetDependency]:
        enabled = True if enabled_only else None
        stmt = (
            select(AssetDependency)
            .options(
                joinedload(AssetDependency.source_asset),
                joinedload(AssetDependency.target_asset),
            )
            .where(
                or_(
                    AssetDependency.source_asset_id == asset_id,
                    AssetDependency.target_asset_id == asset_id,
                )
            )
            .order_by(AssetDependency.id.asc())
        )
        if enabled is not None:
            stmt = stmt.where(AssetDependency.enabled.is_(enabled))
        return list(self._db.scalars(stmt).unique().all())
