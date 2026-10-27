"""Read-only asset dependency graph foundation (Phase 16.0 — no impact propagation)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.domain.models import Asset, AssetDependency
from app.domain.repositories import AssetDependencyRepository, AssetRepository


class AssetDependencyService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._deps = AssetDependencyRepository(db)
        self._assets = AssetRepository(db)

    def list_dependencies(
        self,
        *,
        source_asset_id: str | None = None,
        target_asset_id: str | None = None,
        dependency_type: str | None = None,
        utility_kind: str | None = None,
        zone_id: str | None = None,
        enabled: bool | None = True,
    ) -> list[dict[str, Any]]:
        rows = self._deps.list(
            source_asset_id=source_asset_id,
            target_asset_id=target_asset_id,
            dependency_type=dependency_type,
            utility_kind=utility_kind,
            zone_id=zone_id,
            enabled=enabled,
        )
        return [self._to_out(row) for row in rows]

    def get_dependencies_for_asset(self, asset_id: str) -> dict[str, Any]:
        """
        Return edges involving an asset.

        - depends_on: assets this asset depends on (this asset is the target of supplies)
        - depended_by: assets that depend on this asset (this asset is the source)
        """
        asset = self._assets.get(asset_id)
        if asset is None:
            raise NotFoundError(f"Asset not found: {asset_id}")

        outgoing = self._deps.list_from_source(asset_id, enabled_only=True)
        incoming = self._deps.list_to_target(asset_id, enabled_only=True)

        return {
            "asset_id": asset.id,
            "utility": asset.utility_kind,
            "zone": asset.zone_id,
            "available": True,
            "depends_on": [self._endpoint_ref(edge, endpoint="source") for edge in incoming],
            "depended_by": [self._endpoint_ref(edge, endpoint="target") for edge in outgoing],
            "edges": [self._to_out(e) for e in [*incoming, *outgoing]],
        }

    def simulation_dependencies_payload(self, asset_id: str) -> dict[str, Any]:
        """Shape used by Digital Twin / simulation responses (no impact math)."""
        asset = self._assets.get(asset_id)
        if asset is None:
            return {
                "available": False,
                "message": f"Asset not found: {asset_id}",
                "depends_on": [],
                "depended_by": [],
            }

        view = self.get_dependencies_for_asset(asset_id)
        has_edges = bool(view["depends_on"] or view["depended_by"])
        return {
            "available": True,
            "message": (
                "Explicit dependency edges loaded from asset_dependencies."
                if has_edges
                else "No explicit dependency edges configured for this asset."
            ),
            "depends_on": view["depends_on"],
            "depended_by": view["depended_by"],
        }

    def _to_out(self, edge: AssetDependency) -> dict[str, Any]:
        source = edge.source_asset or self._assets.get(edge.source_asset_id)
        target = edge.target_asset or self._assets.get(edge.target_asset_id)
        if source is None or target is None:
            raise NotFoundError(
                f"Dependency {edge.id} references missing asset "
                f"(source={edge.source_asset_id}, target={edge.target_asset_id})"
            )
        return {
            "id": edge.id,
            "source_asset_id": source.id,
            "source_utility": source.utility_kind,
            "source_zone": source.zone_id,
            "target_asset_id": target.id,
            "target_utility": target.utility_kind,
            "target_zone": target.zone_id,
            "dependency_type": edge.dependency_type,
            "enabled": edge.enabled,
        }

    def _endpoint_ref(self, edge: AssetDependency, *, endpoint: str) -> dict[str, Any]:
        asset: Asset | None
        if endpoint == "source":
            asset = edge.source_asset or self._assets.get(edge.source_asset_id)
        else:
            asset = edge.target_asset or self._assets.get(edge.target_asset_id)
        if asset is None:
            raise NotFoundError(f"Dependency {edge.id} references missing endpoint asset")
        return {
            "dependency_id": edge.id,
            "asset_id": asset.id,
            "asset_name": asset.name,
            "utility": asset.utility_kind,
            "zone": asset.zone_id,
            "dependency_type": edge.dependency_type,
        }
