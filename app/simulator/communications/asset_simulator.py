"""Communications asset simulator — continuous telemetry for seeded Comm assets."""

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
from app.simulator.communications.scenarios import (
    CommunicationsScenarioMode,
    degradation_increment,
    resolve_zone_behavior,
)
from app.simulator.communications.telemetry_generator import CommunicationsTelemetryGenerator
from app.simulator.publishers.mqtt_publisher import MqttPublisher
from app.utilities.communications import metrics as communications_metrics

logger = get_logger(__name__)

# Nominal profile keys seeded in Phase 17.2 — do not invent a second baseline set.
_NOMINAL_BY_METRIC: dict[str, str] = {
    "link_rtt_ms": "nominal_link_rtt_ms",
    "packet_loss_pct": "nominal_packet_loss_pct",
    "cabinet_temp_c": "nominal_cabinet_temp_c",
    "power_draw_w": "nominal_power_draw_w",
}

# Fallback only if profile nominals are missing (should not happen after 17.2 seed).
_DEFAULT_BASELINES: dict[str, dict[str, float]] = {
    "edge_pop": {
        "link_rtt_ms": 12.0,
        "packet_loss_pct": 0.2,
        "cabinet_temp_c": 28.0,
        "power_draw_w": 180.0,
    },
    "iot_gateway": {
        "link_rtt_ms": 18.0,
        "packet_loss_pct": 0.4,
        "cabinet_temp_c": 30.0,
        "power_draw_w": 45.0,
    },
}


class CommunicationsAssetSimulator:
    """
    Iterates Communications assets/sensors and yields MQTT topic + JSON payload pairs.

    ``run_continuous`` persists every reading (direct DB) or publishes to MQTT
    when ``MQTT_ENABLED=true``. Does not invoke health evaluation (Phase 17.4).
    """

    def __init__(
        self,
        db: Session,
        *,
        seed: int | None = 42,
        mode: CommunicationsScenarioMode = CommunicationsScenarioMode.MIXED,
    ) -> None:
        self._db = db
        self._assets = AssetRepository(db)
        self._sensors = SensorRepository(db)
        self._generator = CommunicationsTelemetryGenerator(seed=seed)
        self._mode = mode
        self._topics = topic_config_from_settings(get_settings())

    @property
    def mode(self) -> CommunicationsScenarioMode:
        return self._mode

    @property
    def generator(self) -> CommunicationsTelemetryGenerator:
        return self._generator

    def ramp_scenarios(self) -> None:
        """Advance degrading-zone intensity once per tick (temporal progression)."""
        seen: set[str] = set()
        for asset in self._assets.list(utility_kind="communications"):
            zone_id = asset.zone_id
            if zone_id in seen:
                continue
            seen.add(zone_id)
            behavior = resolve_zone_behavior(self._mode, zone_id)
            self._generator.ramp_zone_intensity(zone_id, degradation_increment(behavior))

    def iter_messages(self) -> Iterator[tuple[str, dict[str, Any], str, str]]:
        """Yield ``(topic, payload, asset_id, utility_kind)``."""
        assets = self._assets.list(utility_kind="communications")
        for asset in assets:
            profile = dict(asset.profile or {})
            metric_defs = communications_metrics.metrics_for_asset_type(asset.type)
            sensors = {s.metric_key: s for s in self._sensors.list_by_asset(asset.id)}
            behavior = resolve_zone_behavior(self._mode, asset.zone_id)
            intensity = self._generator.zone_intensity(asset.zone_id)
            baselines = self._baselines(asset.type, profile)

            values = self._generator.tick_metrics(
                asset_id=asset.id,
                asset_type=asset.type,
                zone_id=asset.zone_id,
                behavior=behavior,
                intensity=intensity,
                baselines=baselines,
            )

            for metric in metric_defs:
                key = metric["key"]
                sensor = sensors.get(key)
                unit = sensor.unit if sensor is not None else metric["unit"]
                envelope = self._generator.envelope(key, unit=unit, value=values[key])
                profile_key = communications_metrics.PROFILE_FIELD_BY_METRIC.get(key)
                if profile_key:
                    profile[profile_key] = envelope["value"]

                topic = self._topics.telemetry_topic(
                    corridor_id=asset.corridor_id,
                    utility_kind=asset.utility_kind,
                    asset_id=asset.id,
                    metric_key=key,
                )
                yield topic, envelope, asset.id, asset.utility_kind

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
                evaluate_gas=False,
                evaluate_communications=False,
            )
            touched.add(asset_id)
            count += 1
        # One rule-based condition pass per asset per tick (not per metric).
        # Phase 17.4: condition only — no Alert sync.
        from app.services.communications_service import CommunicationsService

        comm = CommunicationsService(self._db)
        for asset_id in sorted(touched):
            comm.evaluate_condition(asset_id)
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
    def _baselines(asset_type: str, profile: dict[str, Any]) -> dict[str, float]:
        defaults = _DEFAULT_BASELINES.get(asset_type, _DEFAULT_BASELINES["edge_pop"])
        out: dict[str, float] = {}
        for metric_key, nominal_key in _NOMINAL_BY_METRIC.items():
            nominal = profile.get(nominal_key)
            if isinstance(nominal, (int, float)):
                out[metric_key] = float(nominal)
            else:
                out[metric_key] = float(defaults[metric_key])
        return out


def run_continuous(
    *,
    interval_seconds: float = 5.0,
    max_ticks: int | None = None,
    seed: int | None = 42,
    mode: CommunicationsScenarioMode = CommunicationsScenarioMode.MIXED,
    settings: Settings | None = None,
) -> None:
    """
    Continuously generate Communications telemetry.

    Keeps one simulator instance (and generator/FSM state) across ticks so
    degradation trends and sticky outages remain temporal.
    """
    settings = settings or get_settings()
    publisher: MqttPublisher | None = None
    ticks = 0

    if settings.mqtt_enabled:
        publisher = MqttPublisher(settings)
        publisher.start()
        logger.info(
            "Communications simulator mode=MQTT scenario=%s interval=%ss",
            mode.value,
            interval_seconds,
        )
    else:
        logger.info(
            "Communications simulator mode=DIRECT_DB scenario=%s interval=%ss",
            mode.value,
            interval_seconds,
        )

    db = SessionLocal()
    try:
        sim = CommunicationsAssetSimulator(db, seed=seed, mode=mode)
        while max_ticks is None or ticks < max_ticks:
            try:
                if publisher is not None:
                    count = sim.publish_tick(publisher)
                else:
                    count = sim.persist_tick()
                ticks += 1
                logger.info(
                    "Communications tick %s wrote/published %s telemetry points",
                    ticks,
                    count,
                )
            except Exception:
                logger.exception("Communications simulator tick failed")
                db.rollback()
            time.sleep(interval_seconds)
    finally:
        if publisher is not None:
            publisher.stop()
        db.close()


def dump_sample(messages: list[dict[str, Any]], *, limit: int = 5) -> str:
    sample = messages[:limit]
    return json.dumps(sample, indent=2)
