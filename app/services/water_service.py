"""WaterService — water-specific orchestration (reference for future utilities)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.api.schemas.asset import AssetOut
from app.api.schemas.utility import UtilitySummaryOut
from app.core.errors import NotFoundError, PluginError
from app.domain.repositories import AssetRepository, UtilityRepository
from app.services.asset_service import AssetService
from app.services.utility_service import UtilityService
from app.utilities.registry import registry
from app.utilities.water import health as water_health
from app.utilities.water import metrics as water_metrics


class WaterService:
    """
    Water utility façade.

    Keeps water-specific concerns out of generic routers while reusing
    shared AssetService / UtilityService for persistence access.
    """

    KIND = "water"

    def __init__(self, db: Session) -> None:
        self._db = db
        self._assets = AssetService(db)
        self._utilities = UtilityService(db)
        self._asset_repo = AssetRepository(db)
        self._utility_repo = UtilityRepository(db)

    def plugin(self):
        try:
            return registry.get(self.KIND)
        except PluginError as exc:
            raise NotFoundError("Water utility plugin is not enabled") from exc

    def list_assets(self, *, zone: str | None = None) -> list[AssetOut]:
        return self._assets.list_assets(utility=self.KIND, zone=zone)

    def get_asset(self, asset_id: str) -> AssetOut:
        asset = self._assets.get_asset(asset_id)
        if asset.utility != self.KIND:
            raise NotFoundError(f"Water asset not found: {asset_id}")
        return asset

    def utility_summary(self) -> UtilitySummaryOut:
        summaries = [u for u in self._utilities.list_utilities() if u.kind == self.KIND]
        if not summaries:
            raise NotFoundError("Water utility is not registered in the database")
        return summaries[0]

    def metric_catalog(self) -> list[dict[str, Any]]:
        return self.plugin().metric_catalog()

    def asset_types(self) -> list[str]:
        return self.plugin().asset_types()

    def metrics_for_type(self, asset_type: str) -> list[dict[str, Any]]:
        return water_metrics.metrics_for_asset_type(asset_type)

    def sync_profile_metric(self, asset_id: str, metric_key: str, value: float) -> None:
        """Mirror latest telemetry into Asset.profile and recompute live health/status."""
        asset = self._asset_repo.get(asset_id)
        if asset is None or asset.utility_kind != self.KIND:
            return

        profile = dict(asset.profile or {})
        profile_key = water_metrics.PROFILE_FIELD_BY_METRIC.get(metric_key)
        if profile_key:
            profile[profile_key] = value

        if metric_key == "pump_running":
            profile["pump_status"] = "running" if value >= 0.5 else "stopped"
        elif metric_key == "pump_speed":
            if asset.status != "maintenance":
                profile["pump_status"] = "running" if value > 100 else "stopped"

        # Build metric map from profile for threshold evaluation.
        metrics: dict[str, float] = {}
        for mk, pk in water_metrics.PROFILE_FIELD_BY_METRIC.items():
            raw = profile.get(pk)
            if isinstance(raw, (int, float)):
                metrics[mk] = float(raw)
        # Alias level keys for evaluate_alerts / weighted_health.
        if "current_level" in profile and isinstance(profile["current_level"], (int, float)):
            metrics.setdefault("tank_level", float(profile["current_level"]))
            metrics.setdefault("reservoir_level", float(profile["current_level"]))

        computed = water_health.weighted_health(
            pressure=metrics.get("pressure"),
            level=metrics.get("tank_level") or metrics.get("reservoir_level"),
            flow=metrics.get("flow_rate"),
            pump_running=metrics.get("pump_running"),
            pump_speed=metrics.get("pump_speed"),
        )
        asset.health = water_health.blend_health(float(asset.health), computed)

        alerts = water_health.evaluate_alerts(metrics)
        if asset.status != "maintenance":
            asset.status = water_health.status_from_alerts(alerts, current=asset.status)

        asset.profile = profile
        self._db.flush()
