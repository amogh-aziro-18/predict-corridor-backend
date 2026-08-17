"""Telemetry payload parsing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.api.schemas.telemetry import TelemetryEnvelope
from app.core.logging import get_logger

logger = get_logger(__name__)


def parse_telemetry_payload(raw: bytes, *, default_metric_key: str | None = None) -> TelemetryEnvelope | None:
    """Parse MQTT bytes into a validated TelemetryEnvelope."""
    try:
        data: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Telemetry payload is not valid JSON")
        return None

    if isinstance(data, (int, float)):
        data = {"value": float(data)}

    if not isinstance(data, dict):
        logger.warning("Telemetry payload must be a JSON object")
        return None

    if default_metric_key and not data.get("metric_key"):
        data = {**data, "metric_key": default_metric_key}

    try:
        envelope = TelemetryEnvelope.model_validate(data)
    except ValidationError:
        logger.warning("Telemetry payload failed schema validation: %s", data)
        return None

    if envelope.recorded_at is None:
        envelope = envelope.model_copy(update={"recorded_at": datetime.now(timezone.utc)})
    return envelope


# Backwards-compatible alias used by older Sprint 1 call sites.
def normalize_telemetry_payload(raw: bytes) -> dict[str, Any] | None:
    envelope = parse_telemetry_payload(raw)
    return envelope.model_dump(mode="json") if envelope else None
