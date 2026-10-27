"""Phase 15.0 — Gas simulator temporal telemetry persistence."""

from __future__ import annotations

from sqlalchemy import func

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.models import Asset, Telemetry
from app.simulator.gas.asset_simulator import GasAssetSimulator
from app.simulator.gas.scenarios import (
    MIXED_ZONE_BEHAVIOR,
    GasScenarioMode,
    ZoneGasBehavior,
    resolve_zone_behavior,
)
from app.simulator.gas.telemetry_generator import GasTelemetryGenerator
from app.utilities.gas.seed_catalog import GAS_ASSET_IDS


def _metric_series(
    db,
    *,
    zone_id: str,
    metric_key: str,
    asset_type: str = "gas_compressor",
    tail: int | None = None,
) -> list[float]:
    asset = (
        db.query(Asset)
        .filter(
            Asset.utility_kind == "gas",
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
    values = [float(r.value) for r in rows]
    if tail is not None and tail > 0:
        return values[-tail:]
    return values


def test_mixed_zone_behavior_mapping() -> None:
    assert resolve_zone_behavior(GasScenarioMode.MIXED, "zone-a") == ZoneGasBehavior.NORMAL
    assert resolve_zone_behavior(GasScenarioMode.MIXED, "zone-b") == ZoneGasBehavior.NORMAL
    assert resolve_zone_behavior(GasScenarioMode.MIXED, "zone-c") == ZoneGasBehavior.DEGRADING
    assert (
        resolve_zone_behavior(GasScenarioMode.MIXED, "zone-d")
        == ZoneGasBehavior.STRONG_DEGRADING
    )
    assert set(MIXED_ZONE_BEHAVIOR.keys()) == {"zone-a", "zone-b", "zone-c", "zone-d"}


def test_gas_generator_temporal_and_reproducible() -> None:
    g1 = GasTelemetryGenerator(seed=7)
    g2 = GasTelemetryGenerator(seed=7)
    seq1 = [
        g1.next_value(
            "vibration_mm_s",
            behavior=ZoneGasBehavior.DEGRADING,
            intensity=0.1 * i,
            state_key="CMP-C-01:vibration_mm_s",
            baseline=2.0,
        )
        for i in range(1, 8)
    ]
    seq2 = [
        g2.next_value(
            "vibration_mm_s",
            behavior=ZoneGasBehavior.DEGRADING,
            intensity=0.1 * i,
            state_key="CMP-C-01:vibration_mm_s",
            baseline=2.0,
        )
        for i in range(1, 8)
    ]
    assert seq1 == seq2
    assert any(abs(seq1[i] - seq1[i - 1]) < 2.0 for i in range(1, len(seq1)))
    assert seq1[-1] > seq1[0]


def test_strong_degrading_drifts_faster_than_mild() -> None:
    mild = GasTelemetryGenerator(seed=11)
    strong = GasTelemetryGenerator(seed=11)
    mild_vals: list[float] = []
    strong_vals: list[float] = []
    for i in range(1, 12):
        intensity = min(1.0, 0.08 * i)
        mild_vals.append(
            mild.next_value(
                "compressor_load_pct",
                behavior=ZoneGasBehavior.DEGRADING,
                intensity=intensity,
                state_key="a:compressor_load_pct",
                baseline=58.0,
            )
        )
        strong_vals.append(
            strong.next_value(
                "compressor_load_pct",
                behavior=ZoneGasBehavior.STRONG_DEGRADING,
                intensity=intensity,
                state_key="b:compressor_load_pct",
                baseline=58.0,
            )
        )
    assert strong_vals[-1] > mild_vals[-1]


def test_normal_mode_stays_near_baseline() -> None:
    seed_all()
    db = SessionLocal()
    try:
        sim = GasAssetSimulator(db, seed=42, mode=GasScenarioMode.NORMAL)
        for _ in range(8):
            sim.persist_tick()

        loads = _metric_series(
            db,
            zone_id="zone-a",
            metric_key="compressor_load_pct",
            asset_type="gas_compressor",
            tail=8,
        )
        assert len(loads) >= 8
        # Normal mode: stay within a reasonable band around nominal (~55–62).
        assert min(loads) >= 40.0
        assert max(loads) <= 85.0
        assert max(loads) - min(loads) < 25.0
    finally:
        db.close()


def test_degrading_mode_raises_load_and_vibration() -> None:
    seed_all()
    db = SessionLocal()
    try:
        # Clear prior gas compressor load rows for a clean series by using zone-d.
        sim = GasAssetSimulator(db, seed=99, mode=GasScenarioMode.DEGRADING)
        for _ in range(15):
            sim.persist_tick()

        loads = _metric_series(
            db,
            zone_id="zone-d",
            metric_key="compressor_load_pct",
            asset_type="gas_compressor",
            tail=15,
        )
        vibes = _metric_series(
            db,
            zone_id="zone-d",
            metric_key="vibration_mm_s",
            asset_type="gas_compressor",
            tail=15,
        )
        assert len(loads) >= 15
        assert loads[-1] > loads[0]
        assert vibes[-1] > vibes[0]
    finally:
        db.close()


def test_mixed_mode_differentiates_zones() -> None:
    seed_all()
    db = SessionLocal()
    try:
        from app.domain.models import Sensor

        sim = GasAssetSimulator(db, seed=21, mode=GasScenarioMode.MIXED)
        last_count = 0
        for _ in range(12):
            last_count = sim.persist_tick()
            assert last_count > 0

        # Every point from the latest Gas tick belongs to utility_kind=gas.
        latest_gas = (
            db.query(Telemetry)
            .join(Sensor, Sensor.id == Telemetry.sensor_id)
            .join(Asset, Asset.id == Sensor.asset_id)
            .filter(Asset.utility_kind == "gas")
            .order_by(Telemetry.recorded_at.desc())
            .limit(last_count)
            .all()
        )
        assert len(latest_gas) == last_count
        for row in latest_gas:
            sensor = db.get(Sensor, row.sensor_id)
            assert sensor is not None
            asset = db.get(Asset, sensor.asset_id)
            assert asset is not None
            assert asset.utility_kind == "gas"
            assert row.metric_key in {
                "pressure_bar",
                "flow_rate_nm3_h",
                "temperature_c",
                "compressor_load_pct",
                "vibration_mm_s",
                "leak_indicator",
                "gas_quality_pct",
                "valve_position_pct",
                "compressor_discharge_pressure_bar",
                "power_consumption_kw",
            }

        # Zone D (strong) load should rise more than Zone A (normal) over this run.
        a_loads = _metric_series(
            db,
            zone_id="zone-a",
            metric_key="compressor_load_pct",
            asset_type="gas_compressor",
            tail=12,
        )
        d_loads = _metric_series(
            db,
            zone_id="zone-d",
            metric_key="compressor_load_pct",
            asset_type="gas_compressor",
            tail=12,
        )
        assert len(a_loads) >= 12 and len(d_loads) >= 12
        a_delta = a_loads[-1] - a_loads[0]
        d_delta = d_loads[-1] - d_loads[0]
        assert d_delta > a_delta

        # Profile sync: compressor profile mirrors latest load.
        cmp = db.get(Asset, "CMP-A-01")
        assert cmp is not None and cmp.utility_kind == "gas"
        assert "compressor_load_pct" in (cmp.profile or {})

        assert set(GAS_ASSET_IDS) == {
            a.id for a in db.query(Asset).filter(Asset.utility_kind == "gas").all()
        }
    finally:
        db.close()


def test_gas_persist_tick_writes_gas_metric_keys_only() -> None:
    seed_all()
    db = SessionLocal()
    try:
        from app.domain.models import Sensor

        sim = GasAssetSimulator(db, seed=3, mode=GasScenarioMode.NORMAL)
        sim.persist_tick()
        rows = (
            db.query(Telemetry.metric_key)
            .join(Sensor, Sensor.id == Telemetry.sensor_id)
            .join(Asset, Asset.id == Sensor.asset_id)
            .filter(Asset.utility_kind == "gas")
            .distinct()
            .all()
        )
        keys = {r[0] for r in rows}
        assert "pressure_bar" in keys
        assert "compressor_load_pct" in keys
        assert "leak_indicator" in keys
        assert "pressure" not in keys  # Water key must not appear as Gas metric
        assert "load_pct" not in keys  # Power key must not appear as Gas metric
    finally:
        db.close()
