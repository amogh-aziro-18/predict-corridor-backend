"""Communications telemetry generator — temporal walks + sticky link outage FSM.

POC ASSUMPTION — NOT ENGINEERING STANDARD.
Clamps, dwells, idle power floors, and outage packet-loss values below are
demo simulator parameters for corridor parity — not telecom SLA or NEBS rules.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from app.simulator.communications.scenarios import ZoneCommunicationsBehavior

# ---------------------------------------------------------------------------
# Sticky outage FSM thresholds / dwells (POC ASSUMPTION)
# ---------------------------------------------------------------------------
# Intensity is accumulated 0→1 like Gas. Crossing these enters DEGRADED / DOWN.
DEGRADE_ENTER_INTENSITY = 0.35
OUTAGE_ENTER_INTENSITY = 0.82

# Minimum ticks spent in DOWN and RECOVERING to avoid 1→0→1 flapping.
MIN_DOWN_DWELL_TICKS = 4
MIN_RECOVER_DWELL_TICKS = 4

# Elevated packet-loss clamp while link is DOWN (percent).
DOWN_PACKET_LOSS_PCT = 35.0

# Power moves toward idle/backup floor during DOWN.
# Floor = max(absolute_floor, nominal * POWER_IDLE_FRACTION).
POWER_IDLE_FRACTION = 0.12
POWER_IDLE_FLOOR_W: dict[str, float] = {
    "edge_pop": 20.0,
    "iot_gateway": 5.0,
}

# After recovery completes, zone intensity is pulled down so another outage
# cycle can re-arm gradually (edge-triggered, not instant re-DOWN).
POST_RECOVERY_INTENSITY = 0.20

FROZEN_METRICS: tuple[str, ...] = (
    "link_rtt_ms",
    "packet_loss_pct",
    "link_up",
    "cabinet_temp_c",
    "power_draw_w",
)


class LinkPathState(StrEnum):
    """Sticky path state for link_up — not an independent random boolean."""

    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    RECOVERING = "recovering"


@dataclass
class _AssetLinkState:
    path: LinkPathState = LinkPathState.UP
    dwell_ticks: int = 0
    frozen_rtt_ms: float | None = None
    # Edge-trigger latch: cleared when intensity falls below DEGRADE_ENTER.
    outage_consumed: bool = False


@dataclass
class _MetricWalk:
    values: dict[str, float] = field(default_factory=dict)


class CommunicationsTelemetryGenerator:
    """
    Produces slowly evolving Communications metric values.

    Continuous metrics use mean-reverting walks around seeded nominals.
    ``link_up`` is driven by a sticky UP→DEGRADED→DOWN→RECOVERING→UP machine
    keyed by asset_id — never flipped randomly each tick.
    """

    def __init__(self, *, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._walks: dict[str, _MetricWalk] = {}
        self._zone_intensity: dict[str, float] = {}
        self._link: dict[str, _AssetLinkState] = {}

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

    def set_zone_intensity(self, zone_id: str, intensity: float) -> None:
        """Test / recovery helper — clamp intensity into [0, 1]."""
        self._zone_intensity[zone_id] = max(0.0, min(1.0, intensity))

    def link_state(self, asset_id: str) -> LinkPathState:
        return self._link.setdefault(asset_id, _AssetLinkState()).path

    def tick_metrics(
        self,
        *,
        asset_id: str,
        asset_type: str,
        zone_id: str,
        behavior: ZoneCommunicationsBehavior,
        intensity: float,
        baselines: dict[str, float],
    ) -> dict[str, float]:
        """
        Advance one asset one tick; return all five frozen metric values.

        FSM advances once per call (once per asset per simulator tick).
        """
        link = self._link.setdefault(asset_id, _AssetLinkState())
        walk = self._walks.setdefault(asset_id, _MetricWalk())

        # Clear outage latch once intensity has cooled enough to re-arm.
        if intensity < DEGRADE_ENTER_INTENSITY:
            link.outage_consumed = False

        self._advance_fsm(link, behavior=behavior, intensity=intensity, zone_id=zone_id)

        rtt = self._next_continuous(
            walk,
            "link_rtt_ms",
            baseline=baselines["link_rtt_ms"],
            behavior=behavior,
            intensity=intensity,
            link=link,
        )
        loss = self._next_continuous(
            walk,
            "packet_loss_pct",
            baseline=baselines["packet_loss_pct"],
            behavior=behavior,
            intensity=intensity,
            link=link,
        )
        temp = self._next_continuous(
            walk,
            "cabinet_temp_c",
            baseline=baselines["cabinet_temp_c"],
            behavior=behavior,
            intensity=intensity,
            link=link,
        )
        power = self._next_continuous(
            walk,
            "power_draw_w",
            baseline=baselines["power_draw_w"],
            behavior=behavior,
            intensity=intensity,
            link=link,
            asset_type=asset_type,
        )

        if link.path == LinkPathState.DOWN:
            if link.frozen_rtt_ms is None:
                link.frozen_rtt_ms = rtt
            rtt = link.frozen_rtt_ms
            loss = DOWN_PACKET_LOSS_PCT
            power = self._pull_toward(
                walk.values.get("power_draw_w", power),
                self._power_idle_floor(asset_type, baselines["power_draw_w"]),
                rate=0.25,
            )
            walk.values["power_draw_w"] = power
            walk.values["link_rtt_ms"] = rtt
            walk.values["packet_loss_pct"] = loss
        elif link.path == LinkPathState.RECOVERING:
            progress = min(1.0, link.dwell_ticks / max(1, MIN_RECOVER_DWELL_TICKS))
            frozen = link.frozen_rtt_ms if link.frozen_rtt_ms is not None else rtt
            rtt = frozen + (baselines["link_rtt_ms"] - frozen) * progress
            loss = DOWN_PACKET_LOSS_PCT + (
                baselines["packet_loss_pct"] - DOWN_PACKET_LOSS_PCT
            ) * progress
            idle = self._power_idle_floor(asset_type, baselines["power_draw_w"])
            power = idle + (baselines["power_draw_w"] - idle) * progress
            # Temperature cools gradually toward nominal during recovery.
            temp = temp + (baselines["cabinet_temp_c"] - temp) * (0.12 + 0.25 * progress)
            walk.values["link_rtt_ms"] = rtt
            walk.values["packet_loss_pct"] = loss
            walk.values["power_draw_w"] = power
            walk.values["cabinet_temp_c"] = temp

        link_up = 0.0 if link.path in (LinkPathState.DOWN, LinkPathState.RECOVERING) else 1.0
        walk.values["link_up"] = link_up

        return {
            "link_rtt_ms": round(self._clamp("link_rtt_ms", rtt), 3),
            "packet_loss_pct": round(self._clamp("packet_loss_pct", loss), 3),
            "link_up": float(link_up),
            "cabinet_temp_c": round(self._clamp("cabinet_temp_c", temp), 3),
            "power_draw_w": round(self._clamp("power_draw_w", power), 3),
        }

    def envelope(
        self,
        metric_key: str,
        *,
        unit: str,
        value: float,
    ) -> dict[str, Any]:
        if not math.isfinite(value):
            raise ValueError(f"Non-finite telemetry value for {metric_key}: {value!r}")
        return {
            "value": float(value),
            "unit": unit,
            "quality": "good",
            "metric_key": metric_key,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    def _advance_fsm(
        self,
        link: _AssetLinkState,
        *,
        behavior: ZoneCommunicationsBehavior,
        intensity: float,
        zone_id: str,
    ) -> None:
        if behavior == ZoneCommunicationsBehavior.NORMAL:
            # Normal zones never enter outage; stay UP with link_up=1.
            if link.path != LinkPathState.UP:
                # Finish any in-flight recovery cleanly if mode switched mid-run.
                if link.path == LinkPathState.RECOVERING:
                    link.dwell_ticks += 1
                    if link.dwell_ticks >= MIN_RECOVER_DWELL_TICKS:
                        link.path = LinkPathState.UP
                        link.dwell_ticks = 0
                        link.frozen_rtt_ms = None
                elif link.path == LinkPathState.DOWN:
                    link.dwell_ticks += 1
                    if link.dwell_ticks >= MIN_DOWN_DWELL_TICKS:
                        link.path = LinkPathState.RECOVERING
                        link.dwell_ticks = 0
                else:
                    link.path = LinkPathState.UP
                    link.dwell_ticks = 0
                    link.frozen_rtt_ms = None
            else:
                link.path = LinkPathState.UP
                link.dwell_ticks = 0
            return

        if link.path == LinkPathState.UP:
            if intensity >= OUTAGE_ENTER_INTENSITY and not link.outage_consumed:
                link.path = LinkPathState.DOWN
                link.dwell_ticks = 0
                link.outage_consumed = True
            elif intensity >= DEGRADE_ENTER_INTENSITY:
                link.path = LinkPathState.DEGRADED
                link.dwell_ticks = 0
            return

        if link.path == LinkPathState.DEGRADED:
            if intensity >= OUTAGE_ENTER_INTENSITY and not link.outage_consumed:
                link.path = LinkPathState.DOWN
                link.dwell_ticks = 0
                link.outage_consumed = True
            elif intensity < DEGRADE_ENTER_INTENSITY:
                link.path = LinkPathState.UP
                link.dwell_ticks = 0
            return

        if link.path == LinkPathState.DOWN:
            link.dwell_ticks += 1
            if link.dwell_ticks >= MIN_DOWN_DWELL_TICKS:
                link.path = LinkPathState.RECOVERING
                link.dwell_ticks = 0
            return

        if link.path == LinkPathState.RECOVERING:
            link.dwell_ticks += 1
            if link.dwell_ticks >= MIN_RECOVER_DWELL_TICKS:
                link.path = LinkPathState.UP
                link.dwell_ticks = 0
                link.frozen_rtt_ms = None
                # Cool intensity so the next outage re-arms via gradual ramp.
                self._zone_intensity[zone_id] = min(
                    self._zone_intensity.get(zone_id, intensity),
                    POST_RECOVERY_INTENSITY,
                )

    def _next_continuous(
        self,
        walk: _MetricWalk,
        metric_key: str,
        *,
        baseline: float,
        behavior: ZoneCommunicationsBehavior,
        intensity: float,
        link: _AssetLinkState,
        asset_type: str | None = None,
    ) -> float:
        del asset_type  # reserved for future type-specific walk steps
        healthy = baseline
        base = walk.values.get(metric_key)
        if base is None:
            step = self._step(metric_key)
            base = healthy + self._rng.uniform(-step, step)

        # During DOWN, RTT/loss are overridden by caller; temp still walks.
        if link.path == LinkPathState.DOWN and metric_key in {
            "link_rtt_ms",
            "packet_loss_pct",
            "power_draw_w",
        }:
            walk.values[metric_key] = base
            return base

        if link.path == LinkPathState.RECOVERING and metric_key in {
            "link_rtt_ms",
            "packet_loss_pct",
            "power_draw_w",
        }:
            # Recovery blend applied by caller after this walk tick.
            pass

        step = self._step(metric_key)
        pull_strength = 0.04 if behavior == ZoneCommunicationsBehavior.NORMAL else 0.015
        pull = (healthy - base) * pull_strength
        noise = self._rng.uniform(-step, step)
        drift = self._scenario_drift(metric_key, behavior=behavior, intensity=intensity)
        value = self._clamp(metric_key, base + noise + pull + drift)
        walk.values[metric_key] = value
        return value

    @staticmethod
    def _pull_toward(current: float, target: float, *, rate: float) -> float:
        return current + (target - current) * rate

    @staticmethod
    def _power_idle_floor(asset_type: str, nominal: float) -> float:
        absolute = POWER_IDLE_FLOOR_W.get(asset_type, 5.0)
        return max(absolute, nominal * POWER_IDLE_FRACTION)

    def _scenario_drift(
        self,
        metric_key: str,
        *,
        behavior: ZoneCommunicationsBehavior,
        intensity: float,
    ) -> float:
        if behavior == ZoneCommunicationsBehavior.NORMAL or intensity <= 0:
            return 0.0
        strength = 1.0 if behavior == ZoneCommunicationsBehavior.DEGRADING else 1.75
        i = max(0.0, min(1.0, intensity)) * strength
        # As intensity rises: RTT↑, loss↑, temp↑, mild power↑. link_up is FSM-owned.
        drifts = {
            "link_rtt_ms": 0.55 * i,
            "packet_loss_pct": 0.12 * i,
            "cabinet_temp_c": 0.35 * i,
            "power_draw_w": 1.8 * i,
        }
        return drifts.get(metric_key, 0.0)

    def _step(self, metric_key: str) -> float:
        return {
            "link_rtt_ms": 0.35,
            "packet_loss_pct": 0.04,
            "cabinet_temp_c": 0.30,
            "power_draw_w": 1.5,
            "link_up": 0.0,
        }.get(metric_key, 0.2)

    def _clamp(self, metric_key: str, value: float) -> float:
        bounds = {
            "link_rtt_ms": (1.0, 500.0),
            "packet_loss_pct": (0.0, 100.0),
            "link_up": (0.0, 1.0),
            "cabinet_temp_c": (5.0, 85.0),
            "power_draw_w": (1.0, 500.0),
        }.get(metric_key, (None, None))
        low, high = bounds
        if low is not None:
            value = max(low, value)
        if high is not None:
            value = min(high, value)
        return value
