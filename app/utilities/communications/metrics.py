"""Communications metric catalog and sensor mapping helpers.

Phase 17.1 foundation — catalog only. Threshold / condition bands belong in
``health.py`` (Phase 17.4). No engineering-certified ranges here.
"""

from __future__ import annotations

from typing import Any

# Canonical Communications metric keys (sensors, MQTT, future simulator).
# Frozen Phase 17 contract — do not add deferred metrics (RF, optical, host, util%).
COMMUNICATIONS_METRICS: dict[str, dict[str, str]] = {
    "link_rtt_ms": {"unit": "ms", "label": "Link RTT"},
    "packet_loss_pct": {"unit": "%", "label": "Packet loss"},
    # Discrete path state encoded as float (Telemetry.value is float).
    "link_up": {"unit": "0-1", "label": "Link up"},
    "cabinet_temp_c": {"unit": "°C", "label": "Cabinet temperature"},
    "power_draw_w": {"unit": "W", "label": "Power draw"},
}

# Both initial asset types share the same five-metric family.
_COMM_ASSET_METRICS: list[str] = [
    "link_rtt_ms",
    "packet_loss_pct",
    "link_up",
    "cabinet_temp_c",
    "power_draw_w",
]

ASSET_TYPE_METRICS: dict[str, list[str]] = {
    "edge_pop": list(_COMM_ASSET_METRICS),
    "iot_gateway": list(_COMM_ASSET_METRICS),
}

# Metric key → Asset.profile field for future live state mirroring.
PROFILE_FIELD_BY_METRIC: dict[str, str] = {
    "link_rtt_ms": "link_rtt_ms",
    "packet_loss_pct": "packet_loss_pct",
    "link_up": "link_up",
    "cabinet_temp_c": "cabinet_temp_c",
    "power_draw_w": "power_draw_w",
}


def metrics_for_asset_type(asset_type: str) -> list[dict[str, Any]]:
    keys = ASSET_TYPE_METRICS.get(asset_type, list(_COMM_ASSET_METRICS))
    return [{"key": k, **COMMUNICATIONS_METRICS[k]} for k in keys if k in COMMUNICATIONS_METRICS]


def catalog() -> list[dict[str, Any]]:
    return [{"key": k, **v} for k, v in COMMUNICATIONS_METRICS.items()]
