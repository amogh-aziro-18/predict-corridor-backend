"""Phase 15.1 — Gas rule-based health / condition assessment."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.api.schemas.telemetry import TelemetryEnvelope
from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.enums import AlertStatus
from app.domain.models import Alert, Asset, Telemetry
from app.domain.repositories import AlertRepository, TelemetryRepository
from app.services.alert_service import AlertService
from app.services.gas_service import GasService
from app.services.telemetry_service import TelemetryService
from app.simulator.gas.asset_simulator import GasAssetSimulator
from app.simulator.gas.scenarios import GasScenarioMode
from app.utilities.gas import health as gas_health
from app.utilities.gas.alert_producer import known_gas_alert_codes, sync_gas_asset_alerts
from app.utilities.gas.seed_catalog import GAS_ASSET_IDS


def _seed() -> None:
    seed_all()


def _compressor_nominals() -> dict[str, float]:
    return {
        "nominal_pressure_bar": 28.0,
        "nominal_flow_nm3_h": 4200.0,
        "nominal_discharge_pressure_bar": 32.0,
        "nominal_power_kw": 380.0,
    }


def test_healthy_gas_telemetry_produces_healthy_condition() -> None:
    result = gas_health.evaluate_asset_condition(
        asset_id="CMP-A-01",
        zone_id="zone-a",
        asset_type="gas_compressor",
        metrics={
            "pressure_bar": 28.0,
            "flow_rate_nm3_h": 4200.0,
            "temperature_c": 45.0,
            "compressor_load_pct": 58.0,
            "vibration_mm_s": 2.0,
            "leak_indicator": 0.05,
            "gas_quality_pct": 96.0,
            "compressor_discharge_pressure_bar": 32.0,
            "power_consumption_kw": 380.0,
        },
        nominals=_compressor_nominals(),
    )
    assert result.condition == "healthy"
    assert result.alert_codes == []
    assert result.assessment_kind == "rule_based_condition_assessment"
    assert result.utility_kind == "gas"
    assert result.health_score >= 90.0
    assert "prediction" not in result.assessment_kind
    assert "ml" not in result.assessment_kind.lower()


def test_degraded_gas_telemetry_produces_warning_or_high_risk() -> None:
    series = {
        "compressor_load_pct": [70.0, 74.0, 80.0, 86.0, 90.0],
        "vibration_mm_s": [2.5, 2.9, 3.4, 4.0, 4.8],
        "temperature_c": [55.0, 60.0, 68.0, 74.0, 82.0],
        "leak_indicator": [0.10, 0.18, 0.25, 0.35, 0.48],
        "pressure_bar": [28.0, 27.0, 25.5, 24.0, 22.5],
        "flow_rate_nm3_h": [4200.0, 4100.0, 3950.0, 3800.0, 3600.0],
        "gas_quality_pct": [96.0, 95.0, 93.5, 92.0, 90.5],
    }
    metrics = {k: v[-1] for k, v in series.items()}
    result = gas_health.evaluate_asset_condition(
        asset_id="CMP-C-01",
        zone_id="zone-c",
        asset_type="gas_compressor",
        metrics=metrics,
        series_by_metric=series,
        nominals=_compressor_nominals(),
    )
    assert result.condition in {"warning", "high_risk", "critical"}
    assert result.alert_codes
    assert any(e.slope is not None and e.slope != 0.0 for e in result.evidence)
    assert result.drivers
    assert any(
        c.startswith(("compressor_load_", "vibration_", "leak_indicator_", "temperature_"))
        for c in result.alert_codes
    )


def test_critical_gas_telemetry() -> None:
    result = gas_health.evaluate_asset_condition(
        asset_id="CMP-D-01",
        zone_id="zone-d",
        asset_type="gas_compressor",
        metrics={
            "compressor_load_pct": 102.0,
            "vibration_mm_s": 8.5,
            "temperature_c": 98.0,
            "leak_indicator": 0.82,
            "pressure_bar": 18.0,
            "flow_rate_nm3_h": 2800.0,
            "gas_quality_pct": 83.0,
            "compressor_discharge_pressure_bar": 20.0,
            "power_consumption_kw": 520.0,
        },
        series_by_metric={
            "compressor_load_pct": [85.0, 92.0, 102.0],
            "vibration_mm_s": [5.0, 6.5, 8.5],
            "leak_indicator": [0.50, 0.65, 0.82],
        },
        nominals=_compressor_nominals(),
    )
    assert result.condition == "critical"
    assert any(c.endswith("_critical") for c in result.alert_codes)
    # Blend starts from previous_health=90; one-shot critical still pulls score down.
    assert result.health_score < 75.0
    low_blend = gas_health.evaluate_asset_condition(
        asset_id="CMP-D-01",
        zone_id="zone-d",
        asset_type="gas_compressor",
        metrics={
            "compressor_load_pct": 102.0,
            "vibration_mm_s": 8.5,
            "temperature_c": 98.0,
            "leak_indicator": 0.82,
            "pressure_bar": 18.0,
            "flow_rate_nm3_h": 2800.0,
            "gas_quality_pct": 83.0,
        },
        nominals=_compressor_nominals(),
        previous_health=40.0,
    )
    assert low_blend.health_score < 55.0


def test_temporal_rising_evidence() -> None:
    series = {
        "compressor_load_pct": [76.0, 78.0, 81.0, 84.0, 88.0],
        "vibration_mm_s": [2.9, 3.1, 3.4, 3.8, 4.2],
        "temperature_c": [66.0, 68.0, 71.0, 74.0, 78.0],
        "leak_indicator": [0.26, 0.30, 0.34, 0.39, 0.44],
    }
    result = gas_health.evaluate_asset_condition(
        asset_id="CMP-C-01",
        zone_id="zone-c",
        asset_type="gas_compressor",
        metrics={k: v[-1] for k, v in series.items()},
        series_by_metric=series,
        nominals=_compressor_nominals(),
    )
    rising = {
        "compressor_load_rising",
        "vibration_rising",
        "temperature_rising",
        "leak_indicator_rising",
    }
    assert rising & set(result.alert_codes)
    assert any((e.slope or 0.0) > 0 for e in result.evidence)


def test_asset_type_metric_applicability() -> None:
    """Compressor-only codes must not fire for regulators/feeders lacking those metrics."""
    feeder_alerts = gas_health.evaluate_alerts(
        {
            "pressure_bar": 6.0,
            "flow_rate_nm3_h": 950.0,
            "temperature_c": 26.0,
            "leak_indicator": 0.05,
            "gas_quality_pct": 95.5,
        },
        asset_type="gas_feeder",
        nominals={
            "nominal_pressure_bar": 6.0,
            "nominal_flow_nm3_h": 950.0,
        },
    )
    assert feeder_alerts == []
    assert "compressor_load_elevated" not in feeder_alerts
    assert "vibration_high" not in feeder_alerts
    assert "valve_position_elevated" not in feeder_alerts

    # High load alone without compressor type → no compressor codes.
    no_type = gas_health.evaluate_alerts(
        {"compressor_load_pct": 99.0, "vibration_mm_s": 9.0},
        asset_type="gas_feeder",
    )
    assert "compressor_load_critical" not in no_type
    assert "vibration_critical" not in no_type

    reg = gas_health.evaluate_alerts(
        {
            "pressure_bar": 8.5,
            "flow_rate_nm3_h": 1800.0,
            "valve_position_pct": 92.0,
            "leak_indicator": 0.05,
            "gas_quality_pct": 96.0,
            "temperature_c": 28.0,
        },
        asset_type="gas_regulator_station",
        nominals={
            "nominal_pressure_bar": 8.5,
            "nominal_flow_nm3_h": 1800.0,
        },
    )
    assert "valve_position_high" in reg
    assert "compressor_load_high" not in reg


def test_no_scenario_zone_leakage() -> None:
    """Same healthy metrics in zone-d must remain healthy — not label-driven."""
    result = gas_health.evaluate_asset_condition(
        asset_id="CMP-D-01",
        zone_id="zone-d",
        asset_type="gas_compressor",
        metrics={
            "pressure_bar": 28.0,
            "flow_rate_nm3_h": 4200.0,
            "temperature_c": 45.0,
            "compressor_load_pct": 55.0,
            "vibration_mm_s": 1.8,
            "leak_indicator": 0.04,
            "gas_quality_pct": 96.5,
            "compressor_discharge_pressure_bar": 32.0,
            "power_consumption_kw": 375.0,
        },
        nominals=_compressor_nominals(),
    )
    assert result.condition == "healthy"
    assert result.alert_codes == []
    # Same critical metrics in zone-a must also be critical (symmetric).
    critical_a = gas_health.evaluate_asset_condition(
        asset_id="CMP-A-01",
        zone_id="zone-a",
        asset_type="gas_compressor",
        metrics={
            "compressor_load_pct": 102.0,
            "vibration_mm_s": 8.5,
            "temperature_c": 98.0,
            "leak_indicator": 0.82,
            "pressure_bar": 18.0,
            "flow_rate_nm3_h": 2800.0,
            "gas_quality_pct": 83.0,
        },
        nominals=_compressor_nominals(),
    )
    critical_d = gas_health.evaluate_asset_condition(
        asset_id="CMP-D-01",
        zone_id="zone-d",
        asset_type="gas_compressor",
        metrics={
            "compressor_load_pct": 102.0,
            "vibration_mm_s": 8.5,
            "temperature_c": 98.0,
            "leak_indicator": 0.82,
            "pressure_bar": 18.0,
            "flow_rate_nm3_h": 2800.0,
            "gas_quality_pct": 83.0,
        },
        nominals=_compressor_nominals(),
    )
    assert critical_a.condition == critical_d.condition == "critical"
    assert set(critical_a.alert_codes) == set(critical_d.alert_codes)


def test_unavailable_without_telemetry() -> None:
    result = gas_health.evaluate_asset_condition(
        asset_id="CMP-A-01",
        zone_id="zone-a",
        asset_type="gas_compressor",
        metrics={},
    )
    assert result.condition == "unavailable"


def test_known_gas_codes_match_vocabulary() -> None:
    known = known_gas_alert_codes()
    sample = gas_health.evaluate_alerts(
        {
            "compressor_load_pct": 99.0,
            "vibration_mm_s": 8.0,
            "temperature_c": 96.0,
            "leak_indicator": 0.8,
            "gas_quality_pct": 82.0,
            "pressure_bar": 18.0,
            "flow_rate_nm3_h": 2500.0,
            "compressor_discharge_pressure_bar": 20.0,
            "power_consumption_kw": 550.0,
        },
        slopes={
            "compressor_load_pct": 1.0,
            "vibration_mm_s": 0.2,
            "temperature_c": 0.5,
            "leak_indicator": 0.02,
        },
        asset_type="gas_compressor",
        nominals=_compressor_nominals(),
    )
    assert sample
    assert set(sample) <= known


def test_alert_dedup_not_spam_on_repeated_sync() -> None:
    _seed()
    db = SessionLocal()
    try:
        asset = db.query(Asset).filter(Asset.id == "CMP-C-01").one()
        for row in AlertRepository(db).list(asset_id=asset.id, utility_kind="gas"):
            AlertRepository(db).delete(row)
        db.commit()

        codes = ["vibration_high", "compressor_load_high", "leak_indicator_elevated"]
        sync_gas_asset_alerts(db, asset=asset, codes=codes)
        db.commit()
        first = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.utility_kind == "gas",
                Alert.status == AlertStatus.OPEN.value,
            )
            .count()
        )
        assert first == len(codes)

        sync_gas_asset_alerts(db, asset=asset, codes=codes)
        db.commit()
        second = (
            db.query(Alert)
            .filter(
                Alert.asset_id == asset.id,
                Alert.utility_kind == "gas",
                Alert.status == AlertStatus.OPEN.value,
            )
            .count()
        )
        assert second == first
    finally:
        db.close()


def test_newest_history_window_per_asset() -> None:
    """GasService uses newest HISTORY_LIMIT samples per sensor, not oldest."""
    _seed()
    db = SessionLocal()
    try:
        svc = TelemetryService(db)
        asset_id = "CMP-A-01"
        metric_key = "compressor_load_pct"
        sensor_id = f"SNS-{asset_id}-{metric_key}"
        # Clear prior rows so this test owns the newest window.
        db.query(Telemetry).filter(Telemetry.sensor_id == sensor_id).delete()
        db.commit()

        base = datetime.now(timezone.utc)
        # First 8 older/low, last 12 newer/critical — timestamps strictly ascending.
        for i in range(20):
            value = 50.0 + i if i < 8 else 100.0 + (i - 8)
            svc.persist_envelope(
                asset_id=asset_id,
                metric_key=metric_key,
                envelope=TelemetryEnvelope(
                    value=value,
                    unit="%",
                    quality="good",
                    metric_key=metric_key,
                    recorded_at=base + timedelta(seconds=i),
                ),
                utility_kind="gas",
                commit=False,
                evaluate_gas=False,
            )
        db.commit()

        gas = GasService(db)
        series = gas._recent_series(asset_id, metric_key)
        assert len(series) == gas.HISTORY_LIMIT
        # Newest window must be the high values (100+), not the early 50s.
        assert min(series) >= 100.0
        assert series[-1] >= 110.0

        gas.evaluate_condition(asset_id)
        asset = db.query(Asset).filter(Asset.id == asset_id).one()
        assert asset.profile.get("condition") == "critical"
        assert "compressor_load_critical" in (asset.profile.get("condition_alert_codes") or [])
    finally:
        db.close()


def test_gas_service_evaluate_writes_profile() -> None:
    _seed()
    db = SessionLocal()
    try:
        svc = TelemetryService(db)
        for metric_key, value, unit in (
            ("compressor_load_pct", 91.0, "%"),
            ("vibration_mm_s", 5.2, "mm/s"),
            ("temperature_c", 84.0, "°C"),
            ("leak_indicator", 0.50, "0-1"),
            ("pressure_bar", 22.0, "bar"),
            ("flow_rate_nm3_h", 3500.0, "Nm3/h"),
            ("gas_quality_pct", 90.0, "%"),
            ("compressor_discharge_pressure_bar", 24.0, "bar"),
            ("power_consumption_kw", 480.0, "kW"),
        ):
            for offset in range(5):
                scaled = value - (4 - offset) * (0.5 if metric_key != "leak_indicator" else 0.04)
                svc.persist_envelope(
                    asset_id="CMP-C-01",
                    metric_key=metric_key,
                    envelope=TelemetryEnvelope(
                        value=scaled,
                        unit=unit,
                        quality="good",
                        metric_key=metric_key,
                    ),
                    utility_kind="gas",
                    commit=False,
                )
        db.commit()

        asset = db.query(Asset).filter(Asset.id == "CMP-C-01").one()
        assert asset.profile.get("condition") in {"warning", "high_risk", "critical"}
        assert asset.profile.get("assessment_kind") == "rule_based_condition_assessment"
        assert asset.profile.get("condition_alert_codes")
        assert asset.profile.get("condition_drivers")
        assert asset.health < 95.0
    finally:
        db.close()


def test_simulator_mixed_mode_conditions_from_telemetry() -> None:
    """MIXED: A/B healthy, C degraded+, D critical — verified via drivers/metrics."""
    _seed()
    db = SessionLocal()
    try:
        sim = GasAssetSimulator(db, seed=99, mode=GasScenarioMode.MIXED)
        for _ in range(50):
            sim.persist_tick()

        conditions: dict[str, str] = {}
        health: dict[str, float] = {}
        codes: dict[str, list[str]] = {}
        for asset_id in GAS_ASSET_IDS:
            asset = db.query(Asset).filter(Asset.id == asset_id).one()
            conditions[asset_id] = str((asset.profile or {}).get("condition") or "unavailable")
            health[asset_id] = float(asset.health)
            codes[asset_id] = list((asset.profile or {}).get("condition_alert_codes") or [])

        rank = {"healthy": 0, "warning": 1, "high_risk": 2, "critical": 3, "unavailable": -1}

        # Compressors are the strongest signal assets for mixed-mode validation.
        assert conditions["CMP-A-01"] == "healthy", (
            conditions["CMP-A-01"],
            codes["CMP-A-01"],
        )
        assert conditions["CMP-B-01"] == "healthy", (
            conditions["CMP-B-01"],
            codes["CMP-B-01"],
        )
        assert rank[conditions["CMP-C-01"]] >= 1, (
            conditions["CMP-C-01"],
            codes["CMP-C-01"],
        )
        assert conditions["CMP-D-01"] == "critical", (
            conditions["CMP-D-01"],
            codes["CMP-D-01"],
            health["CMP-D-01"],
        )
        assert rank[conditions["CMP-D-01"]] > rank[conditions["CMP-C-01"]] or (
            conditions["CMP-D-01"] == "critical" and rank[conditions["CMP-C-01"]] >= 1
        )
        assert health["CMP-A-01"] > health["CMP-C-01"]
        assert health["CMP-B-01"] > health["CMP-D-01"]
        assert health["CMP-D-01"] <= health["CMP-C-01"] + 5.0

        # Evidence must be telemetry-derived codes, not empty labels.
        assert codes["CMP-C-01"]
        assert codes["CMP-D-01"]
        assert any(c.endswith(("_high", "_critical", "_elevated", "_rising", "_stress"))
                   or "stress" in c for c in codes["CMP-D-01"])

        # Zone D compressor telemetry must actually cross critical bands.
        d_load = (
            db.query(Telemetry)
            .filter(Telemetry.sensor_id == "SNS-CMP-D-01-compressor_load_pct")
            .order_by(Telemetry.recorded_at.desc())
            .first()
        )
        d_vibe = (
            db.query(Telemetry)
            .filter(Telemetry.sensor_id == "SNS-CMP-D-01-vibration_mm_s")
            .order_by(Telemetry.recorded_at.desc())
            .first()
        )
        d_leak = (
            db.query(Telemetry)
            .filter(Telemetry.sensor_id == "SNS-CMP-D-01-leak_indicator")
            .order_by(Telemetry.recorded_at.desc())
            .first()
        )
        assert d_load is not None and d_vibe is not None and d_leak is not None
        # At least one critical-band metric must be crossed (telemetry proof).
        crossed = (
            float(d_load.value) >= gas_health.LOAD_CRITICAL
            or float(d_vibe.value) >= gas_health.VIBE_CRITICAL
            or float(d_leak.value) >= gas_health.LEAK_CRITICAL
        )
        assert crossed, {
            "load": d_load.value,
            "vibe": d_vibe.value,
            "leak": d_leak.value,
            "codes": codes["CMP-D-01"],
        }

        # C should show elevated bands without requiring zone-id shortcuts.
        c_load = (
            db.query(Telemetry)
            .filter(Telemetry.sensor_id == "SNS-CMP-C-01-compressor_load_pct")
            .order_by(Telemetry.recorded_at.desc())
            .first()
        )
        c_leak = (
            db.query(Telemetry)
            .filter(Telemetry.sensor_id == "SNS-CMP-C-01-leak_indicator")
            .order_by(Telemetry.recorded_at.desc())
            .first()
        )
        assert c_load is not None and c_leak is not None
        assert (
            float(c_load.value) >= gas_health.LOAD_WARN
            or float(c_leak.value) >= gas_health.LEAK_WARN
            or float(
                db.query(Telemetry)
                .filter(Telemetry.sensor_id == "SNS-CMP-C-01-vibration_mm_s")
                .order_by(Telemetry.recorded_at.desc())
                .first()
                .value
            )
            >= gas_health.VIBE_WARN
        )

        active_gas = AlertService(db).list_alerts(
            utility_kind="gas", status=AlertStatus.OPEN.value
        )
        assert active_gas
        zones_with_alerts = {a.zone_id for a in active_gas}
        assert zones_with_alerts & {"zone-c", "zone-d"}
    finally:
        db.close()


def test_simulator_normal_stays_healthy() -> None:
    _seed()
    db = SessionLocal()
    try:
        sim = GasAssetSimulator(db, seed=11, mode=GasScenarioMode.NORMAL)
        for _ in range(20):
            sim.persist_tick()
        for asset_id in ("CMP-A-01", "CMP-C-01", "CMP-D-01"):
            asset = db.query(Asset).filter(Asset.id == asset_id).one()
            cond = (asset.profile or {}).get("condition")
            assert cond == "healthy", (asset_id, cond, asset.profile.get("condition_alert_codes"))
            assert float(asset.health) >= 85.0
    finally:
        db.close()


def test_simulator_degrading_worsens_conditions() -> None:
    _seed()
    db = SessionLocal()
    try:
        sim = GasAssetSimulator(db, seed=22, mode=GasScenarioMode.DEGRADING)
        for _ in range(45):
            sim.persist_tick()
        # All zones degrade in DEGRADING mode — D still stronger via same drifts.
        conditions = {}
        for asset_id in ("CMP-A-01", "CMP-C-01", "CMP-D-01"):
            asset = db.query(Asset).filter(Asset.id == asset_id).one()
            conditions[asset_id] = (asset.profile or {}).get("condition")
        assert conditions["CMP-A-01"] in {"warning", "high_risk", "critical"}
        assert conditions["CMP-C-01"] in {"warning", "high_risk", "critical"}
        assert conditions["CMP-D-01"] in {"warning", "high_risk", "critical"}
    finally:
        db.close()


def test_gas_telemetry_still_persists_with_health() -> None:
    _seed()
    db = SessionLocal()
    try:
        before = db.query(func.count(Telemetry.id)).scalar() or 0
        sim = GasAssetSimulator(db, seed=3, mode=GasScenarioMode.NORMAL)
        count = sim.persist_tick()
        assert count > 0
        after = db.query(func.count(Telemetry.id)).scalar() or 0
        assert after == before + count
        # Per-asset newest window still works after multi-asset tick.
        repo = TelemetryRepository(db)
        rows = repo.list_by_sensor("SNS-CMP-A-01-pressure_bar", limit=5)
        assert rows
        assert rows[0].recorded_at >= rows[-1].recorded_at
    finally:
        db.close()


def test_water_power_unaffected_by_gas_health_module() -> None:
    """Import/smoke: Water and Power health modules still import and evaluate."""
    from app.utilities.power import health as power_health
    from app.utilities.water import health as water_health

    p = power_health.evaluate_asset_condition(
        asset_id="XFMR-A-01",
        zone_id="zone-a",
        asset_type="transformer",
        metrics={
            "load_pct": 55.0,
            "oil_temp_c": 55.0,
            "voltage_kv": 132.0,
            "power_factor": 0.96,
        },
        nominal_voltage_kv=132.0,
    )
    assert p.condition == "healthy"
    assert water_health.evaluate_alerts({"pressure": 3.5, "flow_rate": 100.0}) == []
