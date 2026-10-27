"""Phase 17.3 — Communications simulator temporal telemetry + sticky outage FSM."""

from __future__ import annotations

import math

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.models import Asset, Telemetry
from app.simulator.communications.asset_simulator import CommunicationsAssetSimulator
from app.simulator.communications.scenarios import (
    MIXED_ZONE_BEHAVIOR,
    CommunicationsScenarioMode,
    ZoneCommunicationsBehavior,
    resolve_zone_behavior,
)
from app.simulator.communications.telemetry_generator import (
    DOWN_PACKET_LOSS_PCT,
    FROZEN_METRICS,
    MIN_DOWN_DWELL_TICKS,
    MIN_RECOVER_DWELL_TICKS,
    OUTAGE_ENTER_INTENSITY,
    POWER_IDLE_FLOOR_W,
    POWER_IDLE_FRACTION,
    CommunicationsTelemetryGenerator,
    LinkPathState,
)
from app.utilities.communications.seed_catalog import COMMUNICATIONS_ASSET_IDS


def _baselines_for(asset_type: str) -> dict[str, float]:
    if asset_type == "iot_gateway":
        return {
            "link_rtt_ms": 18.0,
            "packet_loss_pct": 0.4,
            "cabinet_temp_c": 30.0,
            "power_draw_w": 45.0,
        }
    return {
        "link_rtt_ms": 12.0,
        "packet_loss_pct": 0.2,
        "cabinet_temp_c": 28.0,
        "power_draw_w": 180.0,
    }


def _drive_to_down(
    gen: CommunicationsTelemetryGenerator,
    *,
    asset_id: str = "POP-A-01",
    asset_type: str = "edge_pop",
    zone_id: str = "zone-a",
) -> list[dict[str, float]]:
    """Force outage entry and collect ticks through recovery."""
    baselines = _baselines_for(asset_type)
    series: list[dict[str, float]] = []
    # Ramp past outage threshold with degrading behavior.
    for i in range(1, 40):
        intensity = min(1.0, OUTAGE_ENTER_INTENSITY + 0.01) if i >= 3 else min(1.0, 0.2 * i)
        gen.set_zone_intensity(zone_id, intensity)
        values = gen.tick_metrics(
            asset_id=asset_id,
            asset_type=asset_type,
            zone_id=zone_id,
            behavior=ZoneCommunicationsBehavior.DEGRADING,
            intensity=gen.zone_intensity(zone_id),
            baselines=baselines,
        )
        series.append(values)
        if gen.link_state(asset_id) == LinkPathState.UP and any(
            s["link_up"] == 0.0 for s in series
        ):
            # Completed a full outage cycle.
            break
    return series


def test_mixed_zone_behavior_mapping() -> None:
    assert (
        resolve_zone_behavior(CommunicationsScenarioMode.MIXED, "zone-a")
        == ZoneCommunicationsBehavior.NORMAL
    )
    assert (
        resolve_zone_behavior(CommunicationsScenarioMode.MIXED, "zone-b")
        == ZoneCommunicationsBehavior.NORMAL
    )
    assert (
        resolve_zone_behavior(CommunicationsScenarioMode.MIXED, "zone-c")
        == ZoneCommunicationsBehavior.DEGRADING
    )
    assert (
        resolve_zone_behavior(CommunicationsScenarioMode.MIXED, "zone-d")
        == ZoneCommunicationsBehavior.STRONG_DEGRADING
    )
    assert set(MIXED_ZONE_BEHAVIOR.keys()) == {"zone-a", "zone-b", "zone-c", "zone-d"}


def test_all_eight_seeded_assets_simulate() -> None:
    seed_all()
    db = SessionLocal()
    try:
        sim = CommunicationsAssetSimulator(db, seed=42, mode=CommunicationsScenarioMode.NORMAL)
        count = sim.persist_tick()
        # 8 assets × 5 metrics
        assert count == 40

        assets = (
            db.query(Asset)
            .filter(Asset.utility_kind == "communications")
            .all()
        )
        assert {a.id for a in assets} == set(COMMUNICATIONS_ASSET_IDS)

        for asset_id in COMMUNICATIONS_ASSET_IDS:
            keys = {
                r.metric_key
                for r in db.query(Telemetry)
                .filter(Telemetry.sensor_id.like(f"SNS-{asset_id}-%"))
                .all()
            }
            assert keys == set(FROZEN_METRICS)
    finally:
        db.close()


def test_only_five_frozen_metrics_generated() -> None:
    seed_all()
    db = SessionLocal()
    try:
        sim = CommunicationsAssetSimulator(db, seed=7, mode=CommunicationsScenarioMode.MIXED)
        messages = sim.run_once_json()
        keys = {m["payload"]["metric_key"] for m in messages}
        assert keys == set(FROZEN_METRICS)
        forbidden = {
            "bandwidth",
            "utilization",
            "jitter",
            "rssi",
            "cpu",
            "memory",
        }
        assert keys.isdisjoint(forbidden)
    finally:
        db.close()


def test_normal_mode_valid_telemetry_and_link_up_stable() -> None:
    seed_all()
    db = SessionLocal()
    try:
        sim = CommunicationsAssetSimulator(db, seed=42, mode=CommunicationsScenarioMode.NORMAL)
        for _ in range(12):
            sim.persist_tick()

        # Latest point per sensor only — ignore historical rows from other runs.
        for asset_id in COMMUNICATIONS_ASSET_IDS:
            for metric_key in FROZEN_METRICS:
                row = (
                    db.query(Telemetry)
                    .filter(
                        Telemetry.sensor_id == f"SNS-{asset_id}-{metric_key}",
                        Telemetry.metric_key == metric_key,
                    )
                    .order_by(Telemetry.recorded_at.desc(), Telemetry.id.desc())
                    .first()
                )
                assert row is not None
                assert math.isfinite(float(row.value))
                assert not math.isnan(float(row.value))
                if metric_key == "link_up":
                    assert float(row.value) == 1.0

        # Full normal-mode series for one asset: no random link flaps.
        link_series = (
            db.query(Telemetry)
            .filter(
                Telemetry.sensor_id == "SNS-POP-A-01-link_up",
                Telemetry.metric_key == "link_up",
            )
            .order_by(Telemetry.recorded_at.desc(), Telemetry.id.desc())
            .limit(12)
            .all()
        )
        assert len(link_series) == 12
        assert all(float(r.value) == 1.0 for r in link_series)
    finally:
        db.close()


def test_link_up_does_not_randomly_flip_each_tick() -> None:
    gen = CommunicationsTelemetryGenerator(seed=99)
    baselines = _baselines_for("edge_pop")
    ups: list[float] = []
    for _ in range(20):
        vals = gen.tick_metrics(
            asset_id="POP-A-01",
            asset_type="edge_pop",
            zone_id="zone-a",
            behavior=ZoneCommunicationsBehavior.NORMAL,
            intensity=0.0,
            baselines=baselines,
        )
        ups.append(vals["link_up"])
    assert ups == [1.0] * 20
    assert gen.link_state("POP-A-01") == LinkPathState.UP


def test_degrading_mode_produces_temporal_degradation() -> None:
    gen = CommunicationsTelemetryGenerator(seed=11)
    baselines = _baselines_for("edge_pop")
    rtts: list[float] = []
    losses: list[float] = []
    for i in range(1, 16):
        intensity = min(1.0, 0.08 * i)
        vals = gen.tick_metrics(
            asset_id="POP-C-01",
            asset_type="edge_pop",
            zone_id="zone-c",
            behavior=ZoneCommunicationsBehavior.DEGRADING,
            intensity=intensity,
            baselines=baselines,
        )
        # Only sample while still UP/DEGRADED (before DOWN clamps).
        if vals["link_up"] == 1.0:
            rtts.append(vals["link_rtt_ms"])
            losses.append(vals["packet_loss_pct"])
    assert len(rtts) >= 5
    assert rtts[-1] > rtts[0]
    assert losses[-1] >= losses[0]


def test_mixed_mode_coherent_zone_scenarios() -> None:
    seed_all()
    db = SessionLocal()
    try:
        # Shared DB may retain prior Comm ticks (other tests / live simulators).
        # Clear this sensor so assertions cover only this deterministic MIXED run.
        db.query(Telemetry).filter(
            Telemetry.sensor_id == "SNS-POP-A-01-link_up",
            Telemetry.metric_key == "link_up",
        ).delete(synchronize_session=False)
        db.commit()

        sim = CommunicationsAssetSimulator(db, seed=42, mode=CommunicationsScenarioMode.MIXED)
        for _ in range(10):
            sim.persist_tick()

        # Zone A stays link_up=1 under mixed (normal behavior).
        a_links = (
            db.query(Telemetry)
            .filter(
                Telemetry.sensor_id == "SNS-POP-A-01-link_up",
                Telemetry.metric_key == "link_up",
            )
            .all()
        )
        assert a_links
        assert all(float(r.value) == 1.0 for r in a_links)

        # Zone D intensity should have ramped (strong degrading).
        assert sim.generator.zone_intensity("zone-d") > sim.generator.zone_intensity("zone-a")
        assert sim.generator.zone_intensity("zone-a") == 0.0
    finally:
        db.close()


def test_down_state_persists_for_configured_dwell() -> None:
    gen = CommunicationsTelemetryGenerator(seed=3)
    series = _drive_to_down(gen)
    downs = [i for i, v in enumerate(series) if v["link_up"] == 0.0]
    assert downs, "expected at least one DOWN/RECOVERING tick"
    # Longest consecutive zero-link run must cover DOWN + RECOVERING dwells.
    longest = 1
    cur = 1
    for i in range(1, len(series)):
        if series[i]["link_up"] == 0.0 and series[i - 1]["link_up"] == 0.0:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 1
    assert longest >= MIN_DOWN_DWELL_TICKS + MIN_RECOVER_DWELL_TICKS


def test_recovering_persists_before_returning_up() -> None:
    gen = CommunicationsTelemetryGenerator(seed=5)
    asset_id = "GW-B-01"
    baselines = _baselines_for("iot_gateway")
    states: list[LinkPathState] = []
    for i in range(1, 50):
        intensity = 0.9 if i < 20 else gen.zone_intensity("zone-b")
        gen.set_zone_intensity("zone-b", intensity)
        gen.tick_metrics(
            asset_id=asset_id,
            asset_type="iot_gateway",
            zone_id="zone-b",
            behavior=ZoneCommunicationsBehavior.DEGRADING,
            intensity=gen.zone_intensity("zone-b"),
            baselines=baselines,
        )
        states.append(gen.link_state(asset_id))
        if (
            LinkPathState.RECOVERING in states
            and states[-1] == LinkPathState.UP
            and states.count(LinkPathState.RECOVERING) >= MIN_RECOVER_DWELL_TICKS
        ):
            break
    recovering_run = 0
    max_recovering = 0
    for s in states:
        if s == LinkPathState.RECOVERING:
            recovering_run += 1
            max_recovering = max(max_recovering, recovering_run)
        else:
            recovering_run = 0
    assert max_recovering >= MIN_RECOVER_DWELL_TICKS
    assert LinkPathState.UP in states
    assert LinkPathState.DOWN in states


def test_rtt_freezes_during_down() -> None:
    gen = CommunicationsTelemetryGenerator(seed=8)
    baselines = _baselines_for("edge_pop")
    rtts: list[float] = []
    in_down = False
    for i in range(1, 40):
        gen.set_zone_intensity("zone-a", 0.9)
        vals = gen.tick_metrics(
            asset_id="POP-A-01",
            asset_type="edge_pop",
            zone_id="zone-a",
            behavior=ZoneCommunicationsBehavior.STRONG_DEGRADING,
            intensity=0.9,
            baselines=baselines,
        )
        state = gen.link_state("POP-A-01")
        if state == LinkPathState.DOWN:
            in_down = True
            rtts.append(vals["link_rtt_ms"])
        elif in_down:
            break
    assert len(rtts) >= MIN_DOWN_DWELL_TICKS
    assert len(set(rtts)) == 1


def test_packet_loss_elevated_during_down() -> None:
    gen = CommunicationsTelemetryGenerator(seed=8)
    baselines = _baselines_for("edge_pop")
    losses: list[float] = []
    for _ in range(30):
        gen.set_zone_intensity("zone-a", 0.9)
        vals = gen.tick_metrics(
            asset_id="POP-A-01",
            asset_type="edge_pop",
            zone_id="zone-a",
            behavior=ZoneCommunicationsBehavior.STRONG_DEGRADING,
            intensity=0.9,
            baselines=baselines,
        )
        if gen.link_state("POP-A-01") == LinkPathState.DOWN:
            losses.append(vals["packet_loss_pct"])
        elif losses:
            break
    assert losses
    assert all(v == DOWN_PACKET_LOSS_PCT for v in losses)


def test_temperature_continues_during_down() -> None:
    gen = CommunicationsTelemetryGenerator(seed=12)
    baselines = _baselines_for("edge_pop")
    temps: list[float] = []
    for _ in range(40):
        gen.set_zone_intensity("zone-a", 0.9)
        vals = gen.tick_metrics(
            asset_id="POP-A-01",
            asset_type="edge_pop",
            zone_id="zone-a",
            behavior=ZoneCommunicationsBehavior.STRONG_DEGRADING,
            intensity=0.9,
            baselines=baselines,
        )
        if gen.link_state("POP-A-01") == LinkPathState.DOWN:
            temps.append(vals["cabinet_temp_c"])
        elif temps:
            break
    assert len(temps) >= 2
    # Must not freeze like RTT — thermal trajectory keeps moving under degradation drift.
    assert max(temps) - min(temps) > 0.01


def test_power_moves_toward_idle_floor_during_down() -> None:
    gen = CommunicationsTelemetryGenerator(seed=15)
    baselines = _baselines_for("edge_pop")
    floor = max(
        POWER_IDLE_FLOOR_W["edge_pop"],
        baselines["power_draw_w"] * POWER_IDLE_FRACTION,
    )
    powers: list[float] = []
    for _ in range(40):
        gen.set_zone_intensity("zone-a", 0.9)
        vals = gen.tick_metrics(
            asset_id="POP-A-01",
            asset_type="edge_pop",
            zone_id="zone-a",
            behavior=ZoneCommunicationsBehavior.STRONG_DEGRADING,
            intensity=0.9,
            baselines=baselines,
        )
        if gen.link_state("POP-A-01") == LinkPathState.DOWN:
            powers.append(vals["power_draw_w"])
        elif powers:
            break
    assert len(powers) >= 2
    assert powers[-1] < powers[0]
    assert powers[-1] <= baselines["power_draw_w"]
    assert abs(powers[-1] - floor) < abs(powers[0] - floor)


def test_recovery_is_gradual() -> None:
    gen = CommunicationsTelemetryGenerator(seed=21)
    baselines = _baselines_for("iot_gateway")
    recovering_loss: list[float] = []
    recovering_rtt: list[float] = []
    for _ in range(50):
        gen.set_zone_intensity("zone-b", 0.9)
        vals = gen.tick_metrics(
            asset_id="GW-B-01",
            asset_type="iot_gateway",
            zone_id="zone-b",
            behavior=ZoneCommunicationsBehavior.DEGRADING,
            intensity=gen.zone_intensity("zone-b"),
            baselines=baselines,
        )
        if gen.link_state("GW-B-01") == LinkPathState.RECOVERING:
            recovering_loss.append(vals["packet_loss_pct"])
            recovering_rtt.append(vals["link_rtt_ms"])
        elif recovering_loss and gen.link_state("GW-B-01") == LinkPathState.UP:
            break
    assert len(recovering_loss) >= 2
    # Gradual improvement: loss trends down, not an instant snap to nominal on tick 1.
    assert recovering_loss[0] > baselines["packet_loss_pct"]
    assert recovering_loss[-1] < recovering_loss[0]
    assert recovering_rtt[-1] <= recovering_rtt[0] + 0.5


def test_no_nan_or_invalid_values() -> None:
    gen = CommunicationsTelemetryGenerator(seed=1)
    baselines = _baselines_for("edge_pop")
    for i in range(30):
        intensity = min(1.0, 0.1 * i)
        vals = gen.tick_metrics(
            asset_id="POP-D-01",
            asset_type="edge_pop",
            zone_id="zone-d",
            behavior=ZoneCommunicationsBehavior.STRONG_DEGRADING,
            intensity=intensity,
            baselines=baselines,
        )
        assert set(vals.keys()) == set(FROZEN_METRICS)
        for key, value in vals.items():
            assert isinstance(value, float)
            assert math.isfinite(value)
            assert not math.isnan(value)
        assert vals["link_up"] in (0.0, 1.0)


def test_telemetry_keys_exactly_five_contract_metrics() -> None:
    assert set(FROZEN_METRICS) == {
        "link_rtt_ms",
        "packet_loss_pct",
        "link_up",
        "cabinet_temp_c",
        "power_draw_w",
    }


def test_water_power_gas_simulator_modules_untouched_importable() -> None:
    """Smoke: sibling simulators still import; this file does not alter them."""
    from app.simulator.gas.asset_simulator import GasAssetSimulator
    from app.simulator.power.asset_simulator import PowerAssetSimulator
    from app.simulator.water.asset_simulator import WaterAssetSimulator

    assert GasAssetSimulator is not None
    assert PowerAssetSimulator is not None
    assert WaterAssetSimulator is not None
