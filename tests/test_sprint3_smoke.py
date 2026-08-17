"""Sprint 3 offline checks (no DB required for generator/routes)."""

from __future__ import annotations

from app.main import app
from app.simulator.water.telemetry_generator import WaterTelemetryGenerator


def main() -> None:
    gen = WaterTelemetryGenerator(seed=7)
    values = [gen.next_value("tank_level", 78.0, state_key="TNK:tank_level") for _ in range(5)]
    # Smooth walk: consecutive steps should stay small.
    for a, b in zip(values, values[1:]):
        assert abs(a - b) <= 0.5, values

    paths = sorted(app.openapi()["paths"].keys())
    assert "/api/telemetry/latest" in paths
    assert "/api/dashboard/water" in paths
    print("sprint3_smoke_ok", values)


if __name__ == "__main__":
    main()
