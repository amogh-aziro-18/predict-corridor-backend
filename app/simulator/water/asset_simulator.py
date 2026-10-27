"""Water asset simulator — continuous telemetry generation for seeded assets."""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app.api.schemas.telemetry import TelemetryEnvelope
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.domain.repositories import AssetRepository, SensorRepository
from app.ingestion.mqtt.topics import topic_config_from_settings
from app.services.telemetry_service import TelemetryService
from app.scenarios.engine import ScenarioEngine
from app.simulator.publishers.mqtt_publisher import MqttPublisher
from app.simulator.water.telemetry_generator import WaterTelemetryGenerator
from app.utilities.water import metrics as water_metrics

logger = get_logger(__name__)


class WaterAssetSimulator:
    """
    Iterates water assets/sensors and yields MQTT topic + JSON payload pairs.

    ``run_continuous`` persists every reading (direct DB path) or publishes to
    MQTT when ``MQTT_ENABLED=true`` (API ingestion persists on subscribe).
    """

    def __init__(self, db: Session, *, seed: int | None = 42) -> None:
        self._db = db
        self._assets = AssetRepository(db)
        self._sensors = SensorRepository(db)
        self._generator = WaterTelemetryGenerator(seed=seed)
        self._scenario = ScenarioEngine(db)
        self._topics = topic_config_from_settings(get_settings())

    def iter_messages(self) -> Iterator[tuple[str, dict[str, Any], str, str]]:
        """Yield ``(topic, payload, asset_id, utility_kind)``."""
        assets = self._assets.list(utility_kind="water")
        for asset in assets:
            profile = dict(asset.profile or {})
            metric_defs = water_metrics.metrics_for_asset_type(asset.type)
            sensors = {s.metric_key: s for s in self._sensors.list_by_asset(asset.id)}

            # Prefer catalog for this sprint so new metrics (inflow/outflow/pump_running)
            # are emitted even if seed sensors were created earlier.
            for metric in metric_defs:
                sensor = sensors.get(metric["key"])
                unit = sensor.unit if sensor is not None else metric["unit"]
                current = self._profile_value(profile, metric["key"])
                envelope = self._generator.envelope(
                    metric["key"],
                    unit=unit,
                    current=current,
                    status=asset.status,
                    asset_id=asset.id,
                )
                adjusted = self._scenario.adjust_metric(
                    metric_key=metric["key"],
                    asset_type=asset.type,
                    value=float(envelope["value"]),
                    zone_id=asset.zone_id,
                )
                envelope["value"] = adjusted
                # Keep in-tick profile cache coherent for dependent metrics.
                profile_key = water_metrics.PROFILE_FIELD_BY_METRIC.get(metric["key"])
                if profile_key:
                    profile[profile_key] = envelope["value"]

                topic = self._topics.telemetry_topic(
                    corridor_id=asset.corridor_id,
                    utility_kind=asset.utility_kind,
                    asset_id=asset.id,
                    metric_key=metric["key"],
                )
                yield topic, envelope, asset.id, asset.utility_kind

    def run_once_json(self) -> list[dict[str, Any]]:
        return [{"topic": t, "payload": p} for t, p, _, _ in self.iter_messages()]

    def persist_tick(self) -> int:
        """Generate one full tick and persist each point via TelemetryService."""
        self._scenario.ramp_intensity()
        service = TelemetryService(self._db)
        count = 0
        for _topic, payload, asset_id, utility_kind in self.iter_messages():
            envelope = TelemetryEnvelope.model_validate(payload)
            service.persist_envelope(
                asset_id=asset_id,
                metric_key=payload["metric_key"],
                envelope=envelope,
                utility_kind=utility_kind,
                commit=False,
            )
            count += 1
        self._db.commit()
        return count

    def publish_tick(self, publisher: MqttPublisher) -> int:
        """Generate one full tick and publish to MQTT (ingestion persists)."""
        self._scenario.ramp_intensity()
        count = 0
        for topic, payload, _asset_id, _kind in self.iter_messages():
            publisher.publish(topic, payload)
            count += 1
        return count

    @staticmethod
    def _profile_value(profile: dict[str, Any], metric_key: str) -> float | None:
        key = water_metrics.PROFILE_FIELD_BY_METRIC.get(metric_key, metric_key)
        value = profile.get(key)
        if metric_key == "pump_running":
            if isinstance(value, bool):
                return 1.0 if value else 0.0
            status = profile.get("pump_status")
            if status == "running":
                return 1.0
            if status == "stopped":
                return 0.0
        return float(value) if isinstance(value, (int, float)) else None


def run_continuous(
    *,
    interval_seconds: float = 5.0,
    max_ticks: int | None = None,
    seed: int | None = 42,
    settings: Settings | None = None,
) -> None:
    """
    Continuously generate water telemetry.

    - ``MQTT_ENABLED=true`` → publish over MQTT (API subscriber persists).
    - ``MQTT_ENABLED=false`` → persist directly through TelemetryService.
    """
    settings = settings or get_settings()
    publisher: MqttPublisher | None = None
    ticks = 0

    if settings.mqtt_enabled:
        publisher = MqttPublisher(settings)
        publisher.start()
        logger.info("Simulator mode=MQTT interval=%ss", interval_seconds)
    else:
        logger.info("Simulator mode=DIRECT_DB interval=%ss", interval_seconds)

    try:
        while max_ticks is None or ticks < max_ticks:
            db = SessionLocal()
            try:
                sim = WaterAssetSimulator(db, seed=None)
                if publisher is not None:
                    count = sim.publish_tick(publisher)
                else:
                    count = sim.persist_tick()
                ticks += 1
                logger.info("Tick %s wrote/published %s telemetry points", ticks, count)
            except Exception:
                logger.exception("Simulator tick failed")
                db.rollback()
            finally:
                db.close()
            time.sleep(interval_seconds)
    finally:
        if publisher is not None:
            publisher.stop()


def dump_sample(messages: list[dict[str, Any]], *, limit: int = 5) -> str:
    sample = messages[:limit]
    return json.dumps(sample, indent=2)
