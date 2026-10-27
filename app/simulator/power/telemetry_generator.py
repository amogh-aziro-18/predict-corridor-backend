"""Power telemetry value generator — temporal random-walk with zone scenario drift."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

from app.simulator.power.scenarios import ZonePowerBehavior


class PowerTelemetryGenerator:
    """
    Produces slowly evolving Power metric values.

    Normal zones: mean-reverting walk around healthy baselines.
    Degrading zones: same walk plus gradual drift (load/temp up, voltage/pf down)
    scaled by scenario intensity (0–1). State is keyed by ``asset_id:metric_key``.
    """

    def __init__(self, *, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._state: dict[str, float] = {}
        # Per-zone intensity for degrading behaviors (ramps across ticks).
        self._zone_intensity: dict[str, float] = {}

    def zone_intensity(self, zone_id: str) -> float:
        return self._zone_intensity.get(zone_id, 0.0)

    def ramp_zone_intensity(self, zone_id: str, increment: float) -> float:
        if increment <= 0:
            self._zone_intensity.setdefault(zone_id, 0.0)
            return self._zone_intensity[zone_id]
        current = self._zone_intensity.get(zone_id, 0.28)
        next_i = min(1.0, current + increment)
        self._zone_intensity[zone_id] = next_i
        return next_i

    def next_value(
        self,
        metric_key: str,
        current: float | None = None,
        *,
        state_key: str | None = None,
        behavior: ZonePowerBehavior = ZonePowerBehavior.NORMAL,
        intensity: float = 0.0,
        baseline: float | None = None,
    ) -> float:
        key = state_key or metric_key
        healthy = baseline if baseline is not None else self._default(metric_key)

        base = self._state.get(key)
        if base is None:
            base = current if current is not None else healthy
            # Slight asset-specific offset so Zone A ≠ Zone B clones.
            base = healthy + self._rng.uniform(-self._step(metric_key), self._step(metric_key))

        step = self._step(metric_key)
        pull_strength = 0.04 if behavior == ZonePowerBehavior.NORMAL else 0.015
        pull = (healthy - base) * pull_strength
        noise = self._rng.uniform(-step, step)

        drift = self._scenario_drift(metric_key, behavior=behavior, intensity=intensity)
        value = self._clamp(metric_key, base + noise + pull + drift)
        value = round(value, 3)
        self._state[key] = value
        return value

    def envelope(
        self,
        metric_key: str,
        *,
        unit: str,
        current: float | None = None,
        asset_id: str | None = None,
        behavior: ZonePowerBehavior = ZonePowerBehavior.NORMAL,
        intensity: float = 0.0,
        baseline: float | None = None,
    ) -> dict[str, Any]:
        state_key = f"{asset_id}:{metric_key}" if asset_id else metric_key
        value = self.next_value(
            metric_key,
            current,
            state_key=state_key,
            behavior=behavior,
            intensity=intensity,
            baseline=baseline,
        )
        return {
            "value": value,
            "unit": unit,
            "quality": "good",
            "metric_key": metric_key,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    def _scenario_drift(
        self,
        metric_key: str,
        *,
        behavior: ZonePowerBehavior,
        intensity: float,
    ) -> float:
        if behavior == ZonePowerBehavior.NORMAL or intensity <= 0:
            return 0.0
        strength = 1.0 if behavior == ZonePowerBehavior.DEGRADING else 1.75
        i = max(0.0, min(1.0, intensity)) * strength
        # Gradual directional drift — later health/ML will interpret severity.
        drifts = {
            "load_pct": 0.55 * i,
            "oil_temp_c": 0.45 * i,
            "active_power_mw": 0.08 * i,
            "current_a": 2.5 * i,
            "voltage_kv": -0.12 * i,
            "power_factor": -0.004 * i,
            "frequency_hz": -0.01 * i,
        }
        return drifts.get(metric_key, 0.0)

    def _default(self, metric_key: str) -> float:
        return {
            "voltage_kv": 132.0,
            "current_a": 280.0,
            "active_power_mw": 28.0,
            "load_pct": 58.0,
            "frequency_hz": 50.0,
            "oil_temp_c": 58.0,
            "power_factor": 0.95,
        }.get(metric_key, 1.0)

    def _step(self, metric_key: str) -> float:
        return {
            "voltage_kv": 0.15,
            "current_a": 4.0,
            "active_power_mw": 0.35,
            "load_pct": 0.9,
            "frequency_hz": 0.02,
            "oil_temp_c": 0.35,
            "power_factor": 0.004,
        }.get(metric_key, 0.2)

    def _clamp(self, metric_key: str, value: float) -> float:
        bounds = {
            "voltage_kv": (20.0, 145.0),
            "current_a": (0.0, 900.0),
            "active_power_mw": (0.0, 120.0),
            "load_pct": (5.0, 115.0),
            "frequency_hz": (49.0, 51.0),
            "oil_temp_c": (25.0, 110.0),
            "power_factor": (0.70, 1.0),
        }.get(metric_key, (None, None))
        low, high = bounds
        if low is not None:
            value = max(low, value)
        if high is not None:
            value = min(high, value)
        return value
