"""Phase 17.4 — Communications rule-based health / condition assessment."""

from __future__ import annotations

from app.db.seed import seed_all
from app.db.session import SessionLocal
from app.domain.models import Alert, Asset
from app.services.communications_service import CommunicationsService
from app.simulator.communications.asset_simulator import CommunicationsAssetSimulator
from app.simulator.communications.scenarios import CommunicationsScenarioMode
from app.utilities.communications import health as comm_health


def _pop_nominals() -> dict[str, float]:
    return {
        "nominal_link_rtt_ms": 12.0,
        "nominal_packet_loss_pct": 0.2,
        "nominal_cabinet_temp_c": 28.0,
        "nominal_power_draw_w": 180.0,
    }


def _gw_nominals() -> dict[str, float]:
    return {
        "nominal_link_rtt_ms": 18.0,
        "nominal_packet_loss_pct": 0.4,
        "nominal_cabinet_temp_c": 30.0,
        "nominal_power_draw_w": 45.0,
    }


def _healthy_metrics(*, asset: str = "pop") -> dict[str, float]:
    if asset == "gw":
        return {
            "link_rtt_ms": 18.0,
            "packet_loss_pct": 0.3,
            "link_up": 1.0,
            "cabinet_temp_c": 30.0,
            "power_draw_w": 45.0,
        }
    return {
        "link_rtt_ms": 12.0,
        "packet_loss_pct": 0.2,
        "link_up": 1.0,
        "cabinet_temp_c": 28.0,
        "power_draw_w": 180.0,
    }


def test_healthy_edge_pop() -> None:
    result = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics=_healthy_metrics(asset="pop"),
        nominals=_pop_nominals(),
    )
    assert result.condition == "healthy"
    assert result.alert_codes == []
    assert result.utility_kind == "communications"
    assert result.assessment_kind == "rule_based_condition_assessment"
    assert result.health_score >= 90.0
    assert "ml" not in result.assessment_kind.lower()


def test_healthy_iot_gateway() -> None:
    result = comm_health.evaluate_asset_condition(
        asset_id="GW-A-01",
        zone_id="zone-a",
        asset_type="iot_gateway",
        metrics=_healthy_metrics(asset="gw"),
        nominals=_gw_nominals(),
    )
    assert result.condition == "healthy"
    assert result.alert_codes == []
    assert result.health_score >= 90.0


def test_rtt_warning_high_risk_critical() -> None:
    nominals = _pop_nominals()
    warn = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "link_rtt_ms": 12.0 + 8.0},
        nominals=nominals,
    )
    assert warn.condition == "warning"
    assert "rtt_elevated" in warn.alert_codes

    high = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "link_rtt_ms": 12.0 + 20.0},
        nominals=nominals,
    )
    assert high.condition == "high_risk"
    assert "rtt_high" in high.alert_codes

    crit = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "link_rtt_ms": 12.0 + 40.0},
        nominals=nominals,
    )
    assert crit.condition == "critical"
    assert "rtt_critical" in crit.alert_codes


def test_packet_loss_warning_high_risk_critical() -> None:
    warn = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "packet_loss_pct": 1.0},
        nominals=_pop_nominals(),
    )
    assert warn.condition == "warning"
    assert "packet_loss_elevated" in warn.alert_codes

    high = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "packet_loss_pct": 5.0},
        nominals=_pop_nominals(),
    )
    assert high.condition == "high_risk"
    assert "packet_loss_high" in high.alert_codes

    crit = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "packet_loss_pct": 15.0},
        nominals=_pop_nominals(),
    )
    assert crit.condition == "critical"
    assert "packet_loss_critical" in crit.alert_codes


def test_link_up_zero_unavailable() -> None:
    result = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "link_up": 0.0},
        nominals=_pop_nominals(),
    )
    assert result.condition == "unavailable"
    assert "link_down" in result.alert_codes


def test_link_up_zero_dominates_critical_metrics() -> None:
    result = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={
            "link_rtt_ms": 100.0,
            "packet_loss_pct": 40.0,
            "link_up": 0.0,
            "cabinet_temp_c": 70.0,
            "power_draw_w": 20.0,
        },
        nominals=_pop_nominals(),
    )
    assert result.condition == "unavailable"
    assert "rtt_critical" in result.alert_codes or "packet_loss_critical" in result.alert_codes


def test_cabinet_temperature_bands() -> None:
    warn = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "cabinet_temp_c": 40.0},
        nominals=_pop_nominals(),
    )
    assert warn.condition == "warning"
    assert "cabinet_temp_elevated" in warn.alert_codes

    high = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "cabinet_temp_c": 50.0},
        nominals=_pop_nominals(),
    )
    assert high.condition == "high_risk"
    assert "cabinet_temp_high" in high.alert_codes

    crit = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "cabinet_temp_c": 60.0},
        nominals=_pop_nominals(),
    )
    assert crit.condition == "critical"
    assert "cabinet_temp_critical" in crit.alert_codes


def test_power_warning_and_high_risk() -> None:
    # 10% deviation → warning
    warn = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "power_draw_w": 180.0 * 1.10},
        nominals=_pop_nominals(),
    )
    assert warn.condition == "warning"
    assert "power_draw_elevated" in warn.alert_codes

    high = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "power_draw_w": 180.0 * 1.20},
        nominals=_pop_nominals(),
    )
    assert high.condition == "high_risk"
    assert "power_draw_high" in high.alert_codes


def test_power_alone_never_critical() -> None:
    result = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "power_draw_w": 180.0 * 3.0},
        nominals=_pop_nominals(),
    )
    assert result.condition != "critical"
    assert "power_draw_critical" not in result.alert_codes
    assert result.condition == "high_risk"
    assert "power_draw_high" in result.alert_codes


def test_power_ignored_while_link_down() -> None:
    result = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={
            **_healthy_metrics(),
            "link_up": 0.0,
            "power_draw_w": 20.0,  # idle floor — must not raise power codes
        },
        nominals=_pop_nominals(),
    )
    assert result.condition == "unavailable"
    assert "power_draw_elevated" not in result.alert_codes
    assert "power_draw_high" not in result.alert_codes


def test_path_quality_stress_high_risk() -> None:
    result = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={
            **_healthy_metrics(),
            "link_rtt_ms": 12.0 + 20.0,  # rtt_high
            "packet_loss_pct": 1.5,  # packet_loss_elevated
        },
        nominals=_pop_nominals(),
    )
    assert "path_quality_stress" in result.alert_codes
    assert result.condition == "high_risk"


def test_critical_metric_still_critical_with_compound() -> None:
    result = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={
            **_healthy_metrics(),
            "link_rtt_ms": 12.0 + 40.0,
            "packet_loss_pct": 6.0,
        },
        nominals=_pop_nominals(),
    )
    assert "path_quality_stress" in result.alert_codes
    assert result.condition == "critical"
    assert "rtt_critical" in result.alert_codes


def test_rising_codes_require_level_and_slope() -> None:
    series = {
        "link_rtt_ms": [14.0, 18.0, 22.0, 26.0, 30.0],  # delta>=8, slope > 1
        "packet_loss_pct": [0.2, 0.2, 0.2, 0.2, 0.2],
        "link_up": [1.0, 1.0, 1.0, 1.0, 1.0],
        "cabinet_temp_c": [28.0, 28.0, 28.0, 28.0, 28.0],
        "power_draw_w": [180.0, 180.0, 180.0, 180.0, 180.0],
    }
    metrics = {k: v[-1] for k, v in series.items()}
    result = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics=metrics,
        series_by_metric=series,
        nominals=_pop_nominals(),
    )
    assert "rtt_rising" in result.alert_codes


def test_flat_history_does_not_create_rising_codes() -> None:
    series = {
        "link_rtt_ms": [22.0, 22.0, 22.0, 22.0, 22.0],  # elevated but flat
        "packet_loss_pct": [2.0, 2.0, 2.0, 2.0, 2.0],
        "link_up": [1.0, 1.0, 1.0, 1.0, 1.0],
        "cabinet_temp_c": [42.0, 42.0, 42.0, 42.0, 42.0],
        "power_draw_w": [180.0, 180.0, 180.0, 180.0, 180.0],
    }
    metrics = {k: v[-1] for k, v in series.items()}
    result = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics=metrics,
        series_by_metric=series,
        nominals=_pop_nominals(),
    )
    assert "rtt_rising" not in result.alert_codes
    assert "packet_loss_rising" not in result.alert_codes
    assert "cabinet_temp_rising" not in result.alert_codes


def test_recovery_leaves_unavailable() -> None:
    down = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "link_up": 0.0},
        nominals=_pop_nominals(),
        previous_health=40.0,
    )
    assert down.condition == "unavailable"

    up = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics=_healthy_metrics(),
        nominals=_pop_nominals(),
        previous_health=down.health_score,
    )
    assert up.condition == "healthy"
    assert up.condition != "unavailable"


def test_health_score_blends_during_recovery() -> None:
    previous = 40.0
    recovered = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics=_healthy_metrics(),
        nominals=_pop_nominals(),
        previous_health=previous,
    )
    # blend_health(40, ~100, alpha=0.35) ≈ 61 — not an instant snap to 100
    assert recovered.health_score > previous
    assert recovered.health_score < 95.0


def test_edge_pop_nominal_rtt_handling() -> None:
    # Same absolute RTT is worse vs lower POP nominal than GW nominal.
    pop = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={**_healthy_metrics(), "link_rtt_ms": 32.0},  # delta 20 vs 12
        nominals=_pop_nominals(),
    )
    assert "rtt_high" in pop.alert_codes


def test_iot_gateway_nominal_rtt_handling() -> None:
    gw = comm_health.evaluate_asset_condition(
        asset_id="GW-A-01",
        zone_id="zone-a",
        asset_type="iot_gateway",
        metrics={**_healthy_metrics(asset="gw"), "link_rtt_ms": 32.0},  # delta 14 vs 18
        nominals=_gw_nominals(),
    )
    assert "rtt_elevated" in gw.alert_codes
    assert "rtt_high" not in gw.alert_codes


def test_same_telemetry_same_condition_regardless_of_zone() -> None:
    metrics = {**_healthy_metrics(), "packet_loss_pct": 6.0}
    nominals = _pop_nominals()
    results = [
        comm_health.evaluate_asset_condition(
            asset_id="POP-X-01",
            zone_id=zone,
            asset_type="edge_pop",
            metrics=metrics,
            nominals=nominals,
        )
        for zone in ("zone-a", "zone-b", "zone-c", "zone-d")
    ]
    assert len({r.condition for r in results}) == 1
    assert len({tuple(sorted(r.alert_codes)) for r in results}) == 1
    assert results[0].condition == "high_risk"


def test_unavailable_without_telemetry() -> None:
    result = comm_health.evaluate_asset_condition(
        asset_id="POP-A-01",
        zone_id="zone-a",
        asset_type="edge_pop",
        metrics={},
        nominals=_pop_nominals(),
    )
    assert result.condition == "unavailable"


def test_service_evaluate_persists_condition_profile() -> None:
    """Phase 17.4 profile persistence; Alert sync is Phase 17.5 (see phase175 tests)."""
    seed_all()
    db = SessionLocal()
    try:
        sim = CommunicationsAssetSimulator(
            db, seed=42, mode=CommunicationsScenarioMode.NORMAL
        )
        for _ in range(3):
            sim.persist_tick()

        service = CommunicationsService(db)
        for asset_id in ("POP-A-01", "GW-A-01", "POP-D-01", "GW-D-01"):
            service.evaluate_condition(asset_id)

        asset = db.query(Asset).filter(Asset.id == "POP-A-01").one()
        profile = dict(asset.profile or {})
        assert profile.get("condition") in {
            "healthy",
            "warning",
            "high_risk",
            "critical",
            "unavailable",
        }
        assert profile.get("assessment_kind") == "rule_based_condition_assessment"
        assert "condition_alert_codes" in profile
        # Healthy normal-mode assets should not leave open Communications alerts.
        open_comm = [
            a
            for a in db.query(Alert).all()
            if a.utility_kind == "communications"
            and a.asset_id == "POP-A-01"
            and a.status == "open"
        ]
        assert open_comm == []
    finally:
        db.close()


def test_status_mapping_warning_to_degraded() -> None:
    assert (
        comm_health.status_from_condition("warning", current="operational") == "degraded"
    )
    assert (
        comm_health.status_from_condition("high_risk", current="operational") == "degraded"
    )
    assert (
        comm_health.status_from_condition("critical", current="operational") == "degraded"
    )
    assert (
        comm_health.status_from_condition("unavailable", current="operational")
        == "operational"
    )
    assert (
        comm_health.status_from_condition("critical", current="maintenance") == "maintenance"
    )
