"""GasService — Gas-specific orchestration (profile sync + condition assessment)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.api.schemas.asset import AssetOut
from app.api.schemas.utility import UtilitySummaryOut
from app.core.errors import NotFoundError, PluginError
from app.domain.repositories import AssetRepository, TelemetryRepository
from app.services.asset_service import AssetService
from app.services.utility_service import UtilityService
from app.utilities.gas import health as gas_health
from app.utilities.gas import metrics as gas_metrics
from app.utilities.registry import registry


class GasService:
    """Gas utility façade — rule-based condition sync after telemetry persist."""

    KIND = "gas"
    HISTORY_LIMIT = 12

    def __init__(self, db: Session) -> None:
        self._db = db
        self._assets = AssetService(db)
        self._utilities = UtilityService(db)
        self._asset_repo = AssetRepository(db)
        self._telemetry = TelemetryRepository(db)

    def plugin(self):
        try:
            return registry.get(self.KIND)
        except PluginError as exc:
            raise NotFoundError("Gas utility plugin is not enabled") from exc

    def list_assets(self, *, zone: str | None = None) -> list[AssetOut]:
        return self._assets.list_assets(utility=self.KIND, zone=zone)

    def get_asset(self, asset_id: str) -> AssetOut:
        asset = self._assets.get_asset(asset_id)
        if asset.utility != self.KIND:
            raise NotFoundError(f"Gas asset not found: {asset_id}")
        return asset

    def utility_summary(self) -> UtilitySummaryOut:
        summaries = [u for u in self._utilities.list_utilities() if u.kind == self.KIND]
        if not summaries:
            raise NotFoundError("Gas utility is not registered in the database")
        return summaries[0]

    def metric_catalog(self) -> list[dict[str, Any]]:
        return self.plugin().metric_catalog()

    def asset_types(self) -> list[str]:
        return self.plugin().asset_types()

    def _recent_series(self, asset_id: str, metric_key: str) -> list[float]:
        sensor_id = f"SNS-{asset_id}-{metric_key}"
        rows = self._telemetry.list_by_sensor(sensor_id, limit=self.HISTORY_LIMIT)
        # list_by_sensor is newest-first; reverse for chronological slope.
        return [float(r.value) for r in reversed(rows)]

    def sync_profile_metric(
        self,
        asset_id: str,
        metric_key: str,
        value: float,
        *,
        evaluate: bool = True,
    ) -> None:
        """
        Mirror latest Gas telemetry into Asset.profile.

        When ``evaluate=True`` (default), also run rule-based condition assessment
        and alert sync. Simulators may set ``evaluate=False`` and call
        ``evaluate_condition`` once per asset per tick.
        """
        asset = self._asset_repo.get(asset_id)
        if asset is None or asset.utility_kind != self.KIND:
            return

        profile = dict(asset.profile or {})
        profile_key = gas_metrics.PROFILE_FIELD_BY_METRIC.get(metric_key)
        if profile_key:
            profile[profile_key] = value
        asset.profile = profile
        self._db.flush()

        if evaluate:
            self.evaluate_condition(asset_id)

    def evaluate_condition(self, asset_id: str) -> None:
        """Rule-based condition assessment + alert sync from profile + recent history."""
        asset = self._asset_repo.get(asset_id)
        if asset is None or asset.utility_kind != self.KIND:
            return

        profile = dict(asset.profile or {})
        metrics: dict[str, float] = {}
        for mk, pk in gas_metrics.PROFILE_FIELD_BY_METRIC.items():
            raw = profile.get(pk)
            if isinstance(raw, (int, float)):
                metrics[mk] = float(raw)

        series_by_metric: dict[str, list[float]] = {}
        applicable = gas_metrics.ASSET_TYPE_METRICS.get(asset.type, list(metrics.keys()))
        for mk in applicable:
            series = self._recent_series(asset.id, mk)
            if series:
                series_by_metric[mk] = series
                metrics[mk] = series[-1]

        # Drop metrics not applicable to this asset type (e.g. load on feeder).
        metrics = {k: v for k, v in metrics.items() if k in applicable}

        nominals: dict[str, float] = {}
        for key in (
            "nominal_pressure_bar",
            "nominal_flow_nm3_h",
            "nominal_temperature_c",
            "nominal_compressor_load_pct",
            "nominal_vibration_mm_s",
            "nominal_discharge_pressure_bar",
            "nominal_power_kw",
            "nominal_gas_quality_pct",
            "nominal_valve_position_pct",
        ):
            raw = profile.get(key)
            if isinstance(raw, (int, float)):
                nominals[key] = float(raw)

        result = gas_health.evaluate_asset_condition(
            asset_id=asset.id,
            zone_id=asset.zone_id,
            asset_type=asset.type,
            metrics=metrics,
            series_by_metric=series_by_metric,
            nominals=nominals,
            previous_health=float(asset.health),
        )

        asset.health = result.health_score
        if asset.status != "maintenance":
            asset.status = gas_health.status_from_condition(
                result.condition, current=asset.status
            )
        profile["condition"] = result.condition
        profile["condition_drivers"] = list(result.drivers)
        profile["condition_alert_codes"] = list(result.alert_codes)
        profile["assessment_kind"] = result.assessment_kind
        asset.profile = profile
        self._db.flush()

        from app.utilities.gas.alert_producer import sync_gas_asset_alerts

        sync_gas_asset_alerts(self._db, asset=asset, codes=result.alert_codes)
