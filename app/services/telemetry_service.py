"""Telemetry service — persist and query sensor readings."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.api.schemas.telemetry import (
    AssetLatestTelemetryOut,
    MetricLatestOut,
    TelemetryEnvelope,
    TelemetryHistoryOut,
    TelemetryHistoryPointOut,
    TelemetryOut,
)
from app.core.errors import NotFoundError, ValidationAppError
from app.domain.models import Sensor, Telemetry
from app.domain.repositories import AssetRepository, SensorRepository, TelemetryRepository
from app.services.communications_service import CommunicationsService
from app.services.gas_service import GasService
from app.services.power_service import PowerService
from app.services.water_service import WaterService


def _aware_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


class TelemetryService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._telemetry = TelemetryRepository(db)
        self._sensors = SensorRepository(db)
        self._assets = AssetRepository(db)
        self._water = WaterService(db)
        self._power = PowerService(db)
        self._gas = GasService(db)
        self._communications = CommunicationsService(db)

    def persist_envelope(
        self,
        *,
        asset_id: str,
        metric_key: str,
        envelope: TelemetryEnvelope,
        utility_kind: str | None = None,
        commit: bool = True,
        evaluate_power: bool = True,
        evaluate_gas: bool = True,
        evaluate_communications: bool = True,
    ) -> TelemetryOut:
        asset = self._assets.get(asset_id)
        if asset is None:
            raise NotFoundError(f"Asset not found for telemetry: {asset_id}")
        if utility_kind and asset.utility_kind != utility_kind:
            raise ValidationAppError(
                f"Utility mismatch for asset {asset_id}: expected {utility_kind}"
            )

        key = envelope.metric_key or metric_key
        sensor = self._sensors.get_by_asset_metric(asset_id, key)
        if sensor is None:
            sensor = Sensor(
                id=f"SNS-{asset_id}-{key}",
                asset_id=asset_id,
                name=f"{asset.name} · {key}",
                metric_key=key,
                unit=envelope.unit or "",
                enabled=True,
            )
            sensor = self._sensors.upsert(sensor)

        recorded_at = envelope.recorded_at or datetime.now(timezone.utc)
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)

        row = Telemetry(
            id=str(uuid.uuid4()),
            sensor_id=sensor.id,
            metric_key=key,
            value=float(envelope.value),
            unit=envelope.unit or sensor.unit or "",
            quality=envelope.quality or "good",
            recorded_at=recorded_at,
        )
        self._telemetry.add(row)

        if asset.utility_kind == "water":
            self._water.sync_profile_metric(asset_id, key, float(envelope.value))
        elif asset.utility_kind == "power":
            self._power.sync_profile_metric(
                asset_id,
                key,
                float(envelope.value),
                evaluate=evaluate_power,
            )
        elif asset.utility_kind == "gas":
            self._gas.sync_profile_metric(
                asset_id,
                key,
                float(envelope.value),
                evaluate=evaluate_gas,
            )
        elif asset.utility_kind == "communications":
            self._communications.sync_profile_metric(
                asset_id,
                key,
                float(envelope.value),
                evaluate=evaluate_communications,
            )

        if commit:
            self._db.commit()

        return TelemetryOut(
            id=row.id,
            sensor_id=row.sensor_id,
            metric_key=row.metric_key,
            value=row.value,
            unit=row.unit,
            quality=row.quality,
            recorded_at=row.recorded_at,
        )

    def list_for_sensor(self, sensor_id: str, *, limit: int = 100) -> list[TelemetryOut]:
        rows = self._telemetry.list_by_sensor(sensor_id, limit=limit)
        return [
            TelemetryOut(
                id=r.id,
                sensor_id=r.sensor_id,
                metric_key=r.metric_key,
                value=r.value,
                unit=r.unit,
                quality=r.quality,
                recorded_at=r.recorded_at,
            )
            for r in rows
        ]

    def latest_grouped_by_asset(
        self,
        *,
        utility: str | None = "water",
    ) -> list[AssetLatestTelemetryOut]:
        rows = self._telemetry.latest_rows(utility_kind=utility)
        grouped: dict[str, AssetLatestTelemetryOut] = {}
        metrics_by_asset: dict[str, list[MetricLatestOut]] = defaultdict(list)

        for asset_id, asset_name, tel in rows:
            if asset_id not in grouped:
                asset = self._assets.get(asset_id)
                grouped[asset_id] = AssetLatestTelemetryOut(
                    asset_id=asset_id,
                    asset_name=asset_name,
                    zone=asset.zone_id if asset else "",
                    utility=asset.utility_kind if asset else (utility or ""),
                    status=asset.status if asset else "unknown",
                    health_score=asset.health if asset else 0.0,
                    metrics=[],
                )
            metrics_by_asset[asset_id].append(
                MetricLatestOut(
                    metric_key=tel.metric_key,
                    value=tel.value,
                    unit=tel.unit,
                    quality=tel.quality,
                    recorded_at=tel.recorded_at,
                )
            )

        for asset_id, item in grouped.items():
            item.metrics = sorted(metrics_by_asset[asset_id], key=lambda m: m.metric_key)
        return sorted(grouped.values(), key=lambda a: a.asset_id)

    def history(
        self,
        *,
        asset_id: str | None = None,
        metric_key: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1000,
    ) -> TelemetryHistoryOut:
        start = _aware_utc(start)
        end = _aware_utc(end)
        if asset_id and self._assets.get(asset_id) is None:
            raise NotFoundError(f"Asset not found: {asset_id}")

        rows = self._telemetry.history_rows(
            asset_id=asset_id,
            metric_key=metric_key,
            start=start,
            end=end,
            limit=limit,
        )
        points = [
            TelemetryHistoryPointOut(
                id=str(r["id"]),
                sensor_id=str(r["sensor_id"]),
                asset_id=str(r["asset_id"]),
                asset_name=str(r["asset_name"]),
                metric_key=str(r["metric_key"]),
                value=float(r["value"]),
                unit=str(r.get("unit") or ""),
                quality=str(r.get("quality") or "good"),
                recorded_at=r["recorded_at"],
            )
            for r in rows
        ]
        return TelemetryHistoryOut(
            asset_id=asset_id,
            metric_key=metric_key,
            from_time=start,
            to_time=end,
            count=len(points),
            points=points,
        )
