"""Gas asset simulator — continuous telemetry for seeded Gas assets."""

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
from app.simulator.gas.scenarios import (
    GasScenarioMode,
    degradation_increment,
    resolve_zone_behavior,
)
from app.simulator.gas.telemetry_generator import GasTelemetryGenerator
from app.simulator.publishers.mqtt_publisher import MqttPublisher
from app.utilities.gas import metrics as gas_metrics

logger = get_logger(__name__)


class GasAssetSimulator:
    """
    Iterates Gas assets/sensors and yields MQTT topic + JSON payload pairs.

    ``run_continuous`` persists every reading (direct DB) or publishes to MQTT
    when ``MQTT_ENABLED=true``. Does not invoke Water ScenarioEngine.
    Condition assessment runs once per Gas asset per tick (Phase 15.1).
    """

    def __init__(
        self,
        db: Session,
        *,
        seed: int | None = 42,
        mode: GasScenarioMode = GasScenarioMode.MIXED,
    ) -> None:
        self._db = db
        self._assets = AssetRepository(db)
        self._sensors = SensorRepository(db)
        self._generator = GasTelemetryGenerator(seed=seed)
        self._mode = mode
        self._topics = topic_config_from_settings(get_settings())

    @property
    def mode(self) -> GasScenarioMode:
        return self._mode

    def ramp_scenarios(self) -> None:
        """Advance degrading-zone intensity once per tick (temporal progression)."""
        seen: set[str] = set()
        for asset in self._assets.list(utility_kind="gas"):
            zone_id = asset.zone_id
            if zone_id in seen:
                continue
            seen.add(zone_id)
            behavior = resolve_zone_behavior(self._mode, zone_id)
            self._generator.ramp_zone_intensity(zone_id, degradation_increment(behavior))

    def iter_messages(self) -> Iterator[tuple[str, dict[str, Any], str, str]]:
        """Yield ``(topic, payload, asset_id, utility_kind)``."""
        assets = self._assets.list(utility_kind="gas")
        for asset in assets:
            profile = dict(asset.profile or {})
            metric_defs = gas_metrics.metrics_for_asset_type(asset.type)
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
                profile_key = gas_metrics.PROFILE_FIELD_BY_METRIC.get(metric["key"])
                if profile_key:
                    profile[profile_key] = envelope["value"]

                topic = self._topics.telemetry_topic(
                    corridor_id=asset.corridor_id,
                    utility_kind=asset.utility_kind,
                    asset_id=asset.id,
                    metric_key=metric["key"],
                )
                yield topic, envelope, asset.id, asset.utility_kind

            # Persist profile snapshot; preserve GasService condition metadata.
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
                evaluate_gas=False,
            )
            touched.add(asset_id)
            count += 1
        # One rule-based condition pass per asset per tick (not per metric).
        from app.services.gas_service import GasService

        gas = GasService(self._db)
        for asset_id in sorted(touched):
            gas.evaluate_condition(asset_id)
        self._db.commit()
        return count

    def publish_tick(self, publisher: MqttPublisher) -> int:
        """Generate one full tick and publish to MQTT (ingestion persists)."""
        self.ramp_scenarios()
        count = 0
        for topic, payload, _asset_id, _kind in self.iter_messages():
            publisher.publish(topic, payload)
            count += 1
        self._db.commit()
        return count

    @staticmethod
    def _profile_value(profile: dict[str, Any], metric_key: str) -> float | None:
        key = gas_metrics.PROFILE_FIELD_BY_METRIC.get(metric_key, metric_key)
        value = profile.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _baseline(metric_key: str, asset_type: str, profile: dict[str, Any]) -> float:
        """Healthy operating setpoint — asset-aware where profile provides nominals."""
        nominal_map = {
            "pressure_bar": "nominal_pressure_bar",
            "flow_rate_nm3_h": "nominal_flow_nm3_h",
            "temperature_c": "nominal_temperature_c",
            "compressor_load_pct": "nominal_compressor_load_pct",
            "vibration_mm_s": "nominal_vibration_mm_s",
            "compressor_discharge_pressure_bar": "nominal_discharge_pressure_bar",
            "power_consumption_kw": "nominal_power_kw",
            "gas_quality_pct": "nominal_gas_quality_pct",
            "valve_position_pct": "nominal_valve_position_pct",
        }
        profile_key = nominal_map.get(metric_key)
        if profile_key:
            nominal = profile.get(profile_key)
            if isinstance(nominal, (int, float)):
                return float(nominal)

        defaults = {
            "pressure_bar": 28.0 if asset_type == "gas_compressor" else 8.5 if asset_type == "gas_regulator_station" else 6.0,
            "flow_rate_nm3_h": 4200.0 if asset_type == "gas_compressor" else 1800.0 if asset_type == "gas_regulator_station" else 950.0,
            "temperature_c": 45.0 if asset_type == "gas_compressor" else 28.0,
            "compressor_load_pct": 58.0,
            "vibration_mm_s": 2.0,
            "leak_indicator": 0.05,
            "gas_quality_pct": 96.0,
            "valve_position_pct": 55.0,
            "compressor_discharge_pressure_bar": 32.0,
            "power_consumption_kw": 380.0,
        }
        return float(defaults.get(metric_key, 1.0))


def run_continuous(
    *,
    interval_seconds: float = 5.0,
    max_ticks: int | None = None,
    seed: int | None = 42,
    mode: GasScenarioMode = GasScenarioMode.MIXED,
    settings: Settings | None = None,
) -> None:
    """
    Continuously generate Gas telemetry.

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
            "Gas simulator mode=MQTT scenario=%s interval=%ss",
            mode.value,
            interval_seconds,
        )
    else:
        logger.info(
            "Gas simulator mode=DIRECT_DB scenario=%s interval=%ss",
            mode.value,
            interval_seconds,
        )

    db = SessionLocal()
    try:
        sim = GasAssetSimulator(db, seed=seed, mode=mode)
        while max_ticks is None or ticks < max_ticks:
            try:
                if publisher is not None:
                    count = sim.publish_tick(publisher)
                else:
                    count = sim.persist_tick()
                ticks += 1
                logger.info("Gas tick %s wrote/published %s telemetry points", ticks, count)
            except Exception:
                logger.exception("Gas simulator tick failed")
                db.rollback()
            time.sleep(interval_seconds)
    finally:
        if publisher is not None:
            publisher.stop()
        db.close()


def dump_sample(messages: list[dict[str, Any]], *, limit: int = 5) -> str:
    sample = messages[:limit]
    return json.dumps(sample, indent=2)
