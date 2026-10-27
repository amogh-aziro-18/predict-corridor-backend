"""
GIS operational layers — GeoJSON from PostgreSQL registry + live operational joins.

Does not invent coordinates or recalculate health. Reuses asset health/status/risk
from the registry and joins open alerts / active work orders.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.api.schemas.gis_operational import (
    GisGeoJsonFeatureCollectionOut,
    GisOperationalLayersOut,
)
from app.domain.enums import AlertStatus, WorkOrderStatus
from app.domain.models import Asset
from app.domain.repositories import (
    AlertRepository,
    AssetRepository,
    WorkOrderRepository,
    ZoneRepository,
)
from app.integrations.corridor_location import (
    DELHI_ZONE_ANCHORS,
    get_operational_location,
)

_ACTIVE_WO = {
    WorkOrderStatus.OPEN.value,
    WorkOrderStatus.ASSIGNED.value,
    WorkOrderStatus.IN_PROGRESS.value,
    WorkOrderStatus.COMPLETED.value,
}

_SEVERITY_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}


def _operational_severity(
    *,
    status: str,
    risk: str,
    open_alert_max_severity: str | None,
) -> str:
    """
    Presentation severity from existing operational fields — not a second health model.
    """
    status_l = (status or "").lower()
    risk_l = (risk or "").lower()
    alert_l = (open_alert_max_severity or "").lower()

    if status_l == "offline" or risk_l == "critical" or alert_l == "critical":
        return "CRITICAL"
    if (
        status_l in {"degraded", "maintenance"}
        or risk_l == "high"
        or alert_l == "high"
    ):
        return "DEGRADED"
    if risk_l == "medium" or alert_l in {"medium", "low"}:
        return "STABLE"
    if status_l == "operational" and risk_l in {"", "low", "info"}:
        return "HEALTHY"
    return "STABLE"


def _max_severity(values: list[str]) -> str | None:
    if not values:
        return None
    return max(values, key=lambda s: _SEVERITY_RANK.get(s.lower(), 0))


class GisOperationalService:
    """Compose GeoJSON operational layers for MapLibre."""

    def __init__(self, db: Session) -> None:
        self._assets = AssetRepository(db)
        self._zones = ZoneRepository(db)
        self._alerts = AlertRepository(db)
        self._work_orders = WorkOrderRepository(db)

    def get_layers(
        self,
        *,
        utility: str | None = None,
        zone_id: str | None = None,
    ) -> GisOperationalLayersOut:
        op = get_operational_location()
        assets = self._assets.list(
            utility_kind=utility.strip().lower() if utility else None,
            zone_id=zone_id,
        )
        zones = {z.id: z for z in self._zones.list()}

        alert_counts, alert_max = self._alert_index()
        wo_by_asset = self._active_work_order_index()

        features: list[dict[str, Any]] = []
        omitted_no_coords = 0
        for asset in assets:
            if asset.latitude is None or asset.longitude is None:
                omitted_no_coords += 1
                continue
            features.append(
                self._asset_feature(
                    asset,
                    zone_name=zones.get(asset.zone_id).name if asset.zone_id in zones else None,
                    open_alert_count=alert_counts.get(asset.id, 0),
                    open_alert_max_severity=alert_max.get(asset.id),
                    work_order=wo_by_asset.get(asset.id),
                )
            )

        zone_features = self._zone_features(zones)

        notes = [
            "Asset markers use real PostgreSQL registry latitude/longitude only.",
            "Zone markers are operational Delhi corridor anchors — not surveyed cadastral boundaries.",
            "Health, status, and risk come from the asset registry (existing Water/Power state).",
            "Open alert counts and work-order fields are joined from existing tables.",
            "Maintenance risk fields reuse stored registry risk / predicted_failure_days when present — GIS does not run ML.",
            f"Configured corridor context: {op.display_name} ({op.latitude}, {op.longitude}).",
        ]
        if omitted_no_coords:
            notes.append(
                f"{omitted_no_coords} asset(s) omitted from the spatial layer (no registry coordinates)."
            )

        return GisOperationalLayersOut(
            assets=GisGeoJsonFeatureCollectionOut(
                features=features,
                meta={
                    "feature_count": len(features),
                    "omitted_without_coordinates": omitted_no_coords,
                    "coordinate_source": "postgresql_assets.latitude_longitude",
                },
            ),
            zones=GisGeoJsonFeatureCollectionOut(
                features=zone_features,
                meta={
                    "feature_count": len(zone_features),
                    "geometry_kind": "operational_zone_anchor_point",
                    "boundary_surveyed": False,
                },
            ),
            corridor={
                "display_name": op.display_name,
                "city": op.city,
                "country": op.country,
                "latitude": op.latitude,
                "longitude": op.longitude,
                "timezone_hint": op.timezone_hint,
                "method": op.method,
                "note": (
                    "Map center / corridor context only. Individual assets use their own "
                    "registry coordinates — not forced onto this single point."
                ),
            },
            notes=notes,
        )

    def _alert_index(self) -> tuple[dict[str, int], dict[str, str]]:
        active = self._alerts.list(active_only=True)
        counts: dict[str, int] = defaultdict(int)
        severities: dict[str, list[str]] = defaultdict(list)
        for alert in active:
            if not alert.asset_id:
                continue
            if alert.status not in {
                AlertStatus.OPEN.value,
                AlertStatus.ACKNOWLEDGED.value,
            }:
                continue
            counts[alert.asset_id] += 1
            severities[alert.asset_id].append(alert.severity)
        max_sev = {aid: _max_severity(vals) or "info" for aid, vals in severities.items()}
        return dict(counts), max_sev

    def _active_work_order_index(self) -> dict[str, dict[str, Any]]:
        """Latest active work order per asset (open→completed, not closed)."""
        rows = self._work_orders.list()
        out: dict[str, dict[str, Any]] = {}
        for wo in rows:
            if wo.status not in _ACTIVE_WO:
                continue
            if wo.asset_id in out:
                continue  # list is created_at desc
            out[wo.asset_id] = {
                "id": wo.id,
                "status": wo.status,
                "priority": wo.priority,
                "title": wo.title,
                "assigned_technician_id": wo.assigned_technician_id,
            }
        return out

    def _asset_feature(
        self,
        asset: Asset,
        *,
        zone_name: str | None,
        open_alert_count: int,
        open_alert_max_severity: str | None,
        work_order: dict[str, Any] | None,
    ) -> dict[str, Any]:
        severity = _operational_severity(
            status=asset.status,
            risk=asset.risk,
            open_alert_max_severity=open_alert_max_severity,
        )
        return {
            "type": "Feature",
            "id": asset.id,
            "geometry": {
                "type": "Point",
                "coordinates": [float(asset.longitude), float(asset.latitude)],
            },
            "properties": {
                "asset_id": asset.id,
                "asset_name": asset.name,
                "asset_type": asset.type,
                "utility_kind": asset.utility_kind,
                "zone_id": asset.zone_id,
                "zone_name": zone_name,
                "latitude": float(asset.latitude),
                "longitude": float(asset.longitude),
                "health": float(asset.health),
                "status": asset.status,
                "risk": asset.risk,
                "criticality": asset.criticality,
                "operational_severity": severity,
                "open_alert_count": open_alert_count,
                "open_alert_max_severity": open_alert_max_severity,
                "maintenance_risk_label": asset.risk,
                "predicted_failure_days": asset.predicted_failure_days,
                "confidence": float(asset.confidence) if asset.confidence is not None else None,
                "work_order_id": work_order["id"] if work_order else None,
                "work_order_status": work_order["status"] if work_order else None,
                "work_order_title": work_order["title"] if work_order else None,
                "assigned_technician_id": (
                    work_order["assigned_technician_id"] if work_order else None
                ),
                "coordinate_source": "postgresql_asset_registry",
            },
        }

    def _zone_features(self, zones: dict[str, Any]) -> list[dict[str, Any]]:
        features: list[dict[str, Any]] = []
        for zone_id, (lat, lon) in DELHI_ZONE_ANCHORS.items():
            zone = zones.get(zone_id)
            features.append(
                {
                    "type": "Feature",
                    "id": zone_id,
                    "geometry": {
                        "type": "Point",
                        "coordinates": [float(lon), float(lat)],
                    },
                    "properties": {
                        "zone_id": zone_id,
                        "zone_name": zone.name if zone else zone_id,
                        "geometry_kind": "operational_zone_anchor",
                        "boundary_surveyed": False,
                        "note": (
                            "Operational zone anchor within the Delhi corridor — "
                            "not a surveyed geographic boundary polygon."
                        ),
                        "health": float(zone.health) if zone else None,
                        "utility_kinds": list(zone.utility_kinds or []) if zone else [],
                    },
                }
            )
        return features
