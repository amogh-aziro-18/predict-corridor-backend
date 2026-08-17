"""MQTT topic helpers and parsing for corridor telemetry."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True)
class ParsedTelemetryTopic:
    corridor_id: str
    utility_kind: str
    asset_id: str
    metric_key: str


@dataclass(frozen=True)
class TopicConfig:
    """
    Topic layout:

    ``{prefix}/{corridor_id}/utility/{kind}/asset/{asset_id}/metric/{metric_key}``
    """

    prefix: str

    def telemetry_subscription(self) -> str:
        return f"{self.prefix}/+/utility/+/asset/+/metric/+"

    def telemetry_topic(
        self,
        *,
        corridor_id: str,
        utility_kind: str,
        asset_id: str,
        metric_key: str,
    ) -> str:
        return (
            f"{self.prefix}/{corridor_id}/utility/{utility_kind}"
            f"/asset/{asset_id}/metric/{metric_key}"
        )

    def parse_telemetry_topic(self, topic: str) -> ParsedTelemetryTopic | None:
        parts = topic.split("/")
        # prefix may contain no slashes (e.g. "corridor")
        # Expected: [prefix, corridor_id, "utility", kind, "asset", asset_id, "metric", metric_key]
        if len(parts) < 8:
            return None
        # Allow prefix with multiple segments by anchoring from the right markers.
        try:
            utility_idx = parts.index("utility")
            asset_idx = parts.index("asset", utility_idx)
            metric_idx = parts.index("metric", asset_idx)
        except ValueError:
            return None
        if asset_idx != utility_idx + 2 or metric_idx != asset_idx + 2:
            return None
        if metric_idx + 1 >= len(parts):
            return None
        corridor_id = parts[utility_idx - 1] if utility_idx >= 1 else ""
        if not corridor_id:
            return None
        return ParsedTelemetryTopic(
            corridor_id=corridor_id,
            utility_kind=parts[utility_idx + 1],
            asset_id=parts[asset_idx + 1],
            metric_key=parts[metric_idx + 1],
        )


def topic_config_from_settings(settings: Settings) -> TopicConfig:
    return TopicConfig(prefix=settings.mqtt_topic_prefix)
