"""Sprint 2 smoke checks that do not require PostgreSQL."""

from __future__ import annotations

from app.ingestion.mqtt.topics import TopicConfig
from app.ingestion.parsers import parse_telemetry_payload
from app.main import app
from app.simulator.water.telemetry_generator import WaterTelemetryGenerator
from app.utilities.water.seed_catalog import WATER_ASSETS


def main() -> None:
    tc = TopicConfig(prefix="corridor")
    parsed = tc.parse_telemetry_topic(
        "corridor/corridor-01/utility/water/asset/PMP-A-01/metric/pressure"
    )
    assert parsed is not None
    assert parsed.asset_id == "PMP-A-01"
    assert parsed.metric_key == "pressure"

    envelope = parse_telemetry_payload(
        b'{"value": 3.2, "unit": "bar"}',
        default_metric_key="pressure",
    )
    assert envelope is not None
    assert envelope.value == 3.2

    gen = WaterTelemetryGenerator(seed=1)
    assert isinstance(gen.next_value("tank_level", 70.0), float)
    assert len(WATER_ASSETS) == 30

    paths = sorted(app.openapi()["paths"].keys())
    for required in (
        "/api/health",
        "/api/utilities",
        "/api/zones",
        "/api/assets",
        "/api/assets/{asset_id}",
        "/api/corridor",
    ):
        assert required in paths, f"missing route {required}"

    print("sprint2_smoke_ok")
    print("assets", len(WATER_ASSETS))
    print("routes", len(paths))


if __name__ == "__main__":
    main()
