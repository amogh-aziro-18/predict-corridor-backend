"""Water telemetry processors — reserved for threshold/rules (Sprint 3+)."""

from __future__ import annotations

from typing import Any


def process(envelope: dict[str, Any]) -> None:
    """No-op processor hook for Water MQTT envelopes."""
    return None
