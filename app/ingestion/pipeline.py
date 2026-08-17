"""Ingestion pipeline — parse MQTT telemetry and persist via TelemetryService."""

from __future__ import annotations

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.events.bus import event_bus
from app.events.types import TELEMETRY_RECEIVED
from app.ingestion.mqtt.topics import TopicConfig, topic_config_from_settings
from app.ingestion.parsers import parse_telemetry_payload
from app.core.config import get_settings
from app.services.telemetry_service import TelemetryService
from app.utilities.registry import registry

logger = get_logger(__name__)


class IngestionPipeline:
    """
    MQTT → parse topic → validate envelope → persist telemetry → emit event.

    No forecasting or AI. Utility plugins may observe via process_telemetry.
    """

    def __init__(self, topics: TopicConfig | None = None) -> None:
        self._topics = topics or topic_config_from_settings(get_settings())

    def handle_raw(self, *, topic: str, payload: bytes) -> None:
        parsed = self._topics.parse_telemetry_topic(topic)
        if parsed is None:
            logger.warning("Ignoring telemetry on unrecognized topic=%s", topic)
            return

        envelope = parse_telemetry_payload(payload, default_metric_key=parsed.metric_key)
        if envelope is None:
            logger.warning("Dropping unparseable telemetry on topic=%s", topic)
            return

        self.persist(
            corridor_id=parsed.corridor_id,
            utility_kind=parsed.utility_kind,
            asset_id=parsed.asset_id,
            metric_key=parsed.metric_key,
            envelope=envelope,
        )

    def persist(
        self,
        *,
        corridor_id: str,
        utility_kind: str,
        asset_id: str,
        metric_key: str,
        envelope,
    ) -> None:
        db = SessionLocal()
        try:
            service = TelemetryService(db)
            saved = service.persist_envelope(
                asset_id=asset_id,
                metric_key=metric_key,
                envelope=envelope,
                utility_kind=utility_kind,
            )
            event_bus.publish(
                TELEMETRY_RECEIVED,
                {
                    "corridor_id": corridor_id,
                    "utility_kind": utility_kind,
                    "asset_id": asset_id,
                    "metric_key": metric_key,
                    "telemetry_id": saved.id,
                    "value": saved.value,
                },
            )
            try:
                plugin = registry.get(utility_kind)
                plugin.process_telemetry(
                    {
                        "corridor_id": corridor_id,
                        "asset_id": asset_id,
                        "metric_key": metric_key,
                        "value": saved.value,
                        "unit": saved.unit,
                    }
                )
            except Exception:
                # Plugin may not be registered for this kind yet.
                logger.debug("No plugin process_telemetry for kind=%s", utility_kind)
            logger.info(
                "Persisted telemetry asset=%s metric=%s value=%s",
                asset_id,
                metric_key,
                saved.value,
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to persist telemetry for topic asset=%s", asset_id)
        finally:
            db.close()
