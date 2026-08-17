"""MQTT message handlers."""

from __future__ import annotations

from app.core.logging import get_logger
from app.ingestion.pipeline import IngestionPipeline

logger = get_logger(__name__)


class TelemetryHandler:
    """Receives raw MQTT payloads and forwards them to the ingestion pipeline."""

    def __init__(self, pipeline: IngestionPipeline) -> None:
        self._pipeline = pipeline

    def __call__(self, topic: str, payload: bytes) -> None:
        logger.debug("Telemetry message received topic=%s bytes=%s", topic, len(payload))
        self._pipeline.handle_raw(topic=topic, payload=payload)
