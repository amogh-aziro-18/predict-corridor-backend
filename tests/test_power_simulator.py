"""Phase 14.3A — Power simulator temporal telemetry persistence."""

from __future__ import annotations

from sqlalchemy import func

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.models import Asset, Telemetry
from app.simulator.power.asset_simulator import PowerAssetSimulator
from app.simulator.power.scenarios import (
    MIXED_ZONE_BEHAVIOR,
    PowerScenarioMode,
    ZonePowerBehavior,
    resolve_zone_behavior,
)
from app.simulator.power.telemetry_generator import PowerTelemetryGenerator
from app.utilities.power.seed_catalog import POWER_ASSET_IDS


def _metric_series(
    db,
    *,
    zone_id: str,
    metric_key: str,
    asset_type: str = "transformer",
) -> list[float]:
    asset = (
        db.query(Asset)
        .filter(
            Asset.utility_kind == "power",
            Asset.zone_id == zone_id,
            Asset.type == asset_type,
        )
        .one()
    )
    rows = (
        db.query(Telemetry)
        .filter(
            Telemetry.sensor_id == f"SNS-{asset.id}-{metric_key}",
            Telemetry.metric_key == metric_key,
        )
        .order_by(Telemetry.recorded_at.asc(), Telemetry.id.asc())
        .all()
    )
    return [float(r.value) for r in rows]


def test_mixed_zone_behavior_mapping() -> None:
    assert resolve_zone_behavior(PowerScenarioMode.MIXED, "zone-a") == ZonePowerBehavior.NORMAL
    assert resolve_zone_behavior(PowerScenarioMode.MIXED, "zone-b") == ZonePowerBehavior.NORMAL
    assert resolve_zone_behavior(PowerScenarioMode.MIXED, "zone-c") == ZonePowerBehavior.DEGRADING
    assert (
        resolve_zone_behavior(PowerScenarioMode.MIXED, "zone-d")
        == ZonePowerBehavior.STRONG_DEGRADING
    )
    assert set(MIXED_ZONE_BEHAVIOR.keys()) == {"zone-a", "zone-b", "zone-c", "zone-d"}


def test_power_generator_temporal_and_reproducible() -> None:
    g1 = PowerTelemetryGenerator(seed=7)
    g2 = PowerTelemetryGenerator(seed=7)
    seq1 = [
        g1.next_value(
            "oil_temp_c",
            behavior=ZonePowerBehavior.DEGRADING,
            intensity=0.1 * i,
            state_key="XFMR-C-01:oil_temp_c",
        )
        for i in range(1, 8)
    ]
    seq2 = [
        g2.next_value(
            "oil_temp_c",
            behavior=ZonePowerBehavior.DEGRADING,
            intensity=0.1 * i,
            state_key="XFMR-C-01:oil_temp_c",
        )
        for i in range(1, 8)
    ]
    assert seq1 == seq2
    # Temporally correlated — not independent draws from scratch.
    assert any(abs(seq1[i] - seq1[i - 1]) < 5.0 for i in range(1, len(seq1)))
    assert seq1[-1] > seq1[0]


def test_strong_degrading_drifts_faster_than_mild() -> None:
    mild = PowerTelemetryGenerator(seed=11)
    strong = PowerTelemetryGenerator(seed=11)
    mild_vals: list[float] = []
    strong_vals: list[float] = []
    for i in range(1, 12):
        intensity = min(1.0, 0.08 * i)
        mild_vals.append(
            mild.next_value(
                "load_pct",
                behavior=ZonePowerBehavior.DEGRADING,
                intensity=intensity,
                state_key="a:load_pct",
                baseline=58.0,
            )
        )
        strong_vals.append(
            strong.next_value(
                "load_pct",
                behavior=ZonePowerBehavior.STRONG_DEGRADING,
                intensity=intensity,
                state_key="b:load_pct",
                baseline=58.0,
            )
        )
    assert strong_vals[-1] > mild_vals[-1]


def test_power_simulator_persists_all_zones_and_assets() -> None:
    from app.domain.models import Sensor

    seed_all()
    db = SessionLocal()
    try:
        before_count = (
            db.query(func.count(Telemetry.id))
            .join(Sensor, Sensor.id == Telemetry.sensor_id)
            .join(Asset, Asset.id == Sensor.asset_id)
            .filter(Asset.utility_kind == "power")
            .scalar()
        ) or 0

        sim = PowerAssetSimulator(db, seed=42, mode=PowerScenarioMode.MIXED)
        written = sim.persist_tick()
        assert written > 0
        # 12 assets × 5 metrics each = 60 points per tick
        assert written == 60

        after_count = (
            db.query(func.count(Telemetry.id))
            .join(Sensor, Sensor.id == Telemetry.sensor_id)
            .join(Asset, Asset.id == Sensor.asset_id)
            .filter(Asset.utility_kind == "power")
            .scalar()
        )
        assert after_count == before_count + written

        zones = {
            row[0]
            for row in (
                db.query(Asset.zone_id)
                .join(Sensor, Sensor.asset_id == Asset.id)
                .join(Telemetry, Telemetry.sensor_id == Sensor.id)
                .filter(Asset.utility_kind == "power")
                .distinct()
                .all()
            )
        }
        assert zones == {"zone-a", "zone-b", "zone-c", "zone-d"}

        asset_ids = {
            row[0]
            for row in (
                db.query(Asset.id)
                .join(Sensor, Sensor.asset_id == Asset.id)
                .join(Telemetry, Telemetry.sensor_id == Sensor.id)
                .filter(Asset.utility_kind == "power")
                .distinct()
                .all()
            )
        }
        assert asset_ids == set(POWER_ASSET_IDS)
    finally:
        db.close()


def test_mixed_mode_degradation_trends_across_ticks() -> None:
    from app.domain.models import Sensor

    seed_all()
    db = SessionLocal()
    try:
        sim = PowerAssetSimulator(db, seed=99, mode=PowerScenarioMode.MIXED)
        ticks = 8
        for _ in range(ticks):
            assert sim.persist_tick() == 60

        oil_a = _metric_series(db, zone_id="zone-a", metric_key="oil_temp_c")
        oil_c = _metric_series(db, zone_id="zone-c", metric_key="oil_temp_c")
        oil_d = _metric_series(db, zone_id="zone-d", metric_key="oil_temp_c")
        assert len(oil_a) >= ticks
        assert len(oil_c) >= ticks
        assert len(oil_d) >= ticks

        # Use the last `ticks` samples from this run (append-only telemetry).
        a = oil_a[-ticks:]
        c = oil_c[-ticks:]
        d = oil_d[-ticks:]

        # A remains relatively stable (small net change vs C/D rise).
        assert abs(a[-1] - a[0]) < abs(c[-1] - c[0]) + 2.0
        assert c[-1] > c[0]
        assert d[-1] > d[0]
        # D stronger than C over the same horizon.
        assert (d[-1] - d[0]) > (c[-1] - c[0])

        load_a = _metric_series(db, zone_id="zone-a", metric_key="load_pct")[-ticks:]
        load_c = _metric_series(db, zone_id="zone-c", metric_key="load_pct")[-ticks:]
        assert load_c[-1] > load_a[-1] or (load_c[-1] - load_c[0]) > (load_a[-1] - load_a[0])

        # Water assets must not gain Power-named oil_temp sensors accidentally.
        water_oil = (
            db.query(func.count(Telemetry.id))
            .join(Sensor, Sensor.id == Telemetry.sensor_id)
            .join(Asset, Asset.id == Sensor.asset_id)
            .filter(Asset.utility_kind == "water", Telemetry.metric_key == "oil_temp_c")
            .scalar()
        )
        assert water_oil == 0
    finally:
        db.close()


def test_normal_mode_keeps_all_zones_stable_relative_to_mixed() -> None:
    seed_all()
    db = SessionLocal()
    try:
        sim = PowerAssetSimulator(db, seed=5, mode=PowerScenarioMode.NORMAL)
        for _ in range(5):
            sim.persist_tick()
        oil_c = _metric_series(db, zone_id="zone-c", metric_key="oil_temp_c")[-5:]
        # Under NORMAL mode, Zone C should not show strong upward degradation.
        assert oil_c[-1] - oil_c[0] < 4.0
    finally:
        db.close()
