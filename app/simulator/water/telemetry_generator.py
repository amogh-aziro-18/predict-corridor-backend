"""Water telemetry value generator — smooth random-walk simulation (no AI)."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any


class WaterTelemetryGenerator:
    """
    Produces slowly evolving metric values.

    Uses a small-step random walk with light mean reversion so tanks drift
    like ``78 → 77.8 → 77.5`` instead of jumping randomly.
    """

    def __init__(self, *, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        # In-memory last values keyed by ``asset_id:metric_key``.
        self._state: dict[str, float] = {}

    def next_value(
        self,
        metric_key: str,
        current: float | None = None,
        *,
        status: str = "operational",
        state_key: str | None = None,
    ) -> float:
        key = state_key or metric_key

        if status == "maintenance" and metric_key in {
            "flow_rate",
            "pump_speed",
            "pump_running",
            "inflow",
            "outflow",
        }:
            value = 0.0
            self._state[key] = value
            return value

        if metric_key == "pump_running":
            # Derive from recent pump_speed when available; otherwise flip rarely.
            speed_key = key.rsplit(":", 1)[0] + ":pump_speed" if ":" in key else "pump_speed"
            speed = self._state.get(speed_key, current if current is not None else 1400.0)
            value = 1.0 if speed > 100 else 0.0
            self._state[key] = value
            return value

        base = self._state.get(key)
        if base is None:
            base = current if current is not None else self._default(metric_key)

        step = self._step(metric_key)
        # Random walk + gentle pull toward default (mean reversion).
        target = self._default(metric_key)
        pull = (target - base) * 0.02
        delta = self._rng.uniform(-step, step) + pull
        value = self._clamp(metric_key, base + delta)
        value = round(value, 2)
        self._state[key] = value
        return value

    def envelope(
        self,
        metric_key: str,
        *,
        unit: str,
        current: float | None = None,
        status: str = "operational",
        asset_id: str | None = None,
    ) -> dict[str, Any]:
        state_key = f"{asset_id}:{metric_key}" if asset_id else metric_key
        value = self.next_value(
            metric_key,
            current,
            status=status,
            state_key=state_key,
        )
        return {
            "value": value,
            "unit": unit,
            "quality": "good",
            "metric_key": metric_key,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    def _default(self, metric_key: str) -> float:
        return {
            "flow_rate": 500.0,
            "pressure": 3.0,
            "tank_level": 70.0,
            "reservoir_level": 75.0,
            "inflow": 400.0,
            "outflow": 380.0,
            "pump_speed": 1450.0,
            "pump_running": 1.0,
            "chlorine_level": 0.4,
            "turbidity": 1.0,
            "valve_position": 60.0,
        }.get(metric_key, 1.0)

    def _step(self, metric_key: str) -> float:
        """Max absolute change per tick — kept small for natural drift."""
        return {
            "flow_rate": 8.0,
            "pressure": 0.05,
            "tank_level": 0.35,
            "reservoir_level": 0.25,
            "inflow": 6.0,
            "outflow": 6.0,
            "pump_speed": 8.0,
            "pump_running": 0.0,
            "chlorine_level": 0.01,
            "turbidity": 0.05,
            "valve_position": 0.8,
        }.get(metric_key, 0.5)

    def _clamp(self, metric_key: str, value: float) -> float:
        bounds = {
            "flow_rate": (0.0, 2500.0),
            "pressure": (0.0, 8.0),
            "tank_level": (5.0, 100.0),
            "reservoir_level": (10.0, 100.0),
            "inflow": (0.0, 2500.0),
            "outflow": (0.0, 2500.0),
            "pump_speed": (0.0, 1800.0),
            "pump_running": (0.0, 1.0),
            "chlorine_level": (0.05, 1.2),
            "turbidity": (0.1, 10.0),
            "valve_position": (0.0, 100.0),
        }.get(metric_key, (None, None))
        low, high = bounds
        if low is not None:
            value = max(low, value)
        if high is not None:
            value = min(high, value)
        return value
