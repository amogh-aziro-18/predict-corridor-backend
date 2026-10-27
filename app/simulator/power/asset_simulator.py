"""Power asset simulator — continuous telemetry for seeded Power assets."""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

from sqlalchemy.orm import Session, attributes

from app.api.schemas.telemetry import TelemetryEnvelope
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.domain.repositories import AssetRepository, SensorRepository
from app.ingestion.mqtt.topics import topic_config_from_settings
from app.services.telemetry_service import TelemetryService
from app.simulator.power.scenarios import (
    PowerScenarioMode,
    degradation_increment,
    resolve_zone_behavior,
)
from app.simulator.power.telemetry_generator import PowerTelemetryGenerator
from app.simulator.publishers.mqtt_publisher import MqttPublisher
from app.utilities.power import metrics as power_metrics

logger = get_logger(__name__)


class PowerAssetSimulator:
    """
    Iterates Power assets/sensors and yields MQTT topic + JSON payload pairs.

    ``run_continuous`` persists every reading (direct DB) or publishes to MQTT
    when ``MQTT_ENABLED=true``. Does not invoke Water ScenarioEngine or health.
    """

    def __init__(
        self,
        db: Session,
        *,
        seed: int | None = 42,
        mode: PowerScenarioMode = PowerScenarioMode.MIXED,
    ) -> None:
        self._db = db
        self._assets = AssetRepository(db)
        self._sensors = SensorRepository(db)
        self._generator = PowerTelemetryGenerator(seed=seed)
        self._mode = mode
        self._topics = topic_config_from_settings(get_settings())

    @property
    def mode(self) -> PowerScenarioMode:
        return self._mode

    def ramp_scenarios(self) -> None:
        """Advance degrading-zone intensity once per tick (temporal progression)."""
        seen: set[str] = set()
        for asset in self._assets.list(utility_kind="power"):
            zone_id = asset.zone_id
            if zone_id in seen:
                continue
            seen.add(zone_id)
            behavior = resolve_zone_behavior(self._mode, zone_id)
            self._generator.ramp_zone_intensity(
                zone_id, degradation_increment(behavior)
            )

    def iter_messages(self) -> Iterator[tuple[str, dict[str, Any], str, str]]:
        """Yield ``(topic, payload, asset_id, utility_kind)``."""
        assets = self._assets.list(utility_kind="power")
        for asset in assets:
            profile = dict(asset.profile or {})
            metric_defs = power_metrics.metrics_for_asset_type(asset.type)
            sensors = {s.metric_key: s for s in self._sensors.list_by_asset(asset.id)}
            behavior = resolve_zone_behavior(self._mode, asset.zone_id)
            intensity = self._generator.zone_intensity(asset.zone_id)

            for metric in metric_defs:
                sensor = sensors.get(metric["key"])
                unit = sensor.unit if sensor is not None else metric["unit"]
                current = self._profile_value(profile, metric["key"])
                baseline = self._baseline(metric["key"], asset.type, profile)
                envelope = self._generator.envelope(
                    metric["key"],
                    unit=unit,
                    current=current,
                    asset_id=asset.id,
                    behavior=behavior,
                    intensity=intensity,
                    baseline=baseline,
                )
                profile_key = power_metrics.PROFILE_FIELD_BY_METRIC.get(metric["key"])
                if profile_key:
                    profile[profile_key] = envelope["value"]

                topic = self._topics.telemetry_topic(
                    corridor_id=asset.corridor_id,
                    utility_kind=asset.utility_kind,
                    asset_id=asset.id,
                    metric_key=metric["key"],
                )
                yield topic, envelope, asset.id, asset.utility_kind

            # Persist profile snapshot so the next process can resume temporally.
            # Preserve PowerService condition metadata written during this tick's persists.
            current = dict(asset.profile or {})
            for key in (
                "condition",
                "condition_drivers",
                "condition_alert_codes",
                "assessment_kind",
            ):
                if key in current:
                    profile[key] = current[key]
            asset.profile = profile
            attributes.flag_modified(asset, "profile")

    def run_once_json(self) -> list[dict[str, Any]]:
        return [{"topic": t, "payload": p} for t, p, _, _ in self.iter_messages()]

    def persist_tick(self) -> int:
        """Generate one full tick and persist each point via TelemetryService."""
        self.ramp_scenarios()
        service = TelemetryService(self._db)
        count = 0
        touched: set[str] = set()
        for _topic, payload, asset_id, utility_kind in self.iter_messages():
            envelope = TelemetryEnvelope.model_validate(payload)
            service.persist_envelope(
                asset_id=asset_id,
                metric_key=payload["metric_key"],
                envelope=envelope,
                utility_kind=utility_kind,
                commit=False,
                evaluate_power=False,
            )
            touched.add(asset_id)
            count += 1
        # One rule-based condition pass per asset per tick (not per metric).
        from app.services.power_service import PowerService

        power = PowerService(self._db)
        for asset_id in sorted(touched):
            power.evaluate_condition(asset_id)
        self._db.commit()
        return count

    def publish_tick(self, publisher: MqttPublisher) -> int:
        """Generate one full tick and publish to MQTT (ingestion persists)."""
        self.ramp_scenarios()
        count = 0
        for topic, payload, _asset_id, _kind in self.iter_messages():
            publisher.publish(topic, payload)
            count += 1
        self._db.commit()  # profile snapshots
        return count

    @staticmethod
    def _profile_value(profile: dict[str, Any], metric_key: str) -> float | None:
        key = power_metrics.PROFILE_FIELD_BY_METRIC.get(metric_key, metric_key)
        value = profile.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _baseline(metric_key: str, asset_type: str, profile: dict[str, Any]) -> float:
        """Healthy operating setpoint — asset-aware where profile provides ratings."""
        if metric_key == "voltage_kv":
            nominal = profile.get("nominal_voltage_kv")
            if isinstance(nominal, (int, float)):
                return float(nominal)
            return 33.0 if asset_type == "feeder" else 132.0
        if metric_key == "active_power_mw":
            capacity = profile.get("capacity_mva") or profile.get("rating_mva")
            if isinstance(capacity, (int, float)) and capacity > 0:
                return float(capacity) * 0.55
            return 18.0 if asset_type == "feeder" else 28.0
        if metric_key == "current_a":
            rated = profile.get("rated_current_a")
            if isinstance(rated, (int, float)) and rated > 0:
                return float(rated) * 0.55
            return 280.0
        if metric_key == "load_pct":
            return 58.0
        if metric_key == "oil_temp_c":
            return 58.0
        if metric_key == "frequency_hz":
            return 50.0
        if metric_key == "power_factor":
            return 0.95
        return 1.0


def run_continuous(
    *,
    interval_seconds: float = 5.0,
    max_ticks: int | None = None,
    seed: int | None = 42,
    mode: PowerScenarioMode = PowerScenarioMode.MIXED,
    settings: Settings | None = None,
) -> None:
    """
    Continuously generate Power telemetry.

    Keeps one simulator instance (and generator state) across ticks so
    degradation trends remain temporal. Water ScenarioEngine is not used.
    """
    settings = settings or get_settings()
    publisher: MqttPublisher | None = None
    ticks = 0

    if settings.mqtt_enabled:
        publisher = MqttPublisher(settings)
        publisher.start()
        logger.info(
            "Power simulator mode=MQTT scenario=%s interval=%ss",
            mode.value,
            interval_seconds,
        )
    else:
        logger.info(
            "Power simulator mode=DIRECT_DB scenario=%s interval=%ss",
            mode.value,
            interval_seconds,
        )

    db = SessionLocal()
    try:
        # Shared generator state across ticks (temporal degradation).
        sim = PowerAssetSimulator(db, seed=seed, mode=mode)
        while max_ticks is None or ticks < max_ticks:
            try:
                if publisher is not None:
                    count = sim.publish_tick(publisher)
                else:
                    count = sim.persist_tick()
                ticks += 1
                logger.info("Power tick %s wrote/published %s telemetry points", ticks, count)
            except Exception:
                logger.exception("Power simulator tick failed")
                db.rollback()
            time.sleep(interval_seconds)
    finally:
        if publisher is not None:
            publisher.stop()
        db.close()


def dump_sample(messages: list[dict[str, Any]], *, limit: int = 5) -> str:
    sample = messages[:limit]
    return json.dumps(sample, indent=2)
