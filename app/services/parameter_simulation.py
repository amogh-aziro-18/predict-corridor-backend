"""
Parameter what-if rules (Phase 14.14 + Gas + Communications extensions).

Transparent, explainable operational projections using existing Water/Power/Gas/
Communications threshold bands. Not a physics solver. Not random.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.utilities.communications import health as communications_health
from app.utilities.gas import health as gas_health
from app.utilities.power import health as power_health
from app.utilities.water import health as water_health

# Supported parameter keys by utility (only if baseline telemetry exists).
POWER_PARAMETERS = ("load_pct", "voltage_kv", "oil_temp_c")
WATER_PARAMETERS = ("pressure", "tank_level", "reservoir_level", "flow_rate", "pump_speed")
GAS_PARAMETERS = (
    "compressor_load_pct",
    "vibration_mm_s",
    "temperature_c",
    "leak_indicator",
    "gas_quality_pct",
    "pressure_bar",
    "flow_rate_nm3_h",
    "valve_position_pct",
)
COMMUNICATIONS_PARAMETERS = (
    "link_rtt_ms",
    "packet_loss_pct",
    "link_up",
    "cabinet_temp_c",
    "power_draw_w",
)

# Relative-band metrics → Asset.profile nominal field (same as live health).
GAS_NOMINAL_PROFILE_KEYS: dict[str, str] = {
    "pressure_bar": "nominal_pressure_bar",
    "flow_rate_nm3_h": "nominal_flow_nm3_h",
}
COMMUNICATIONS_NOMINAL_PROFILE_KEYS: dict[str, str] = {
    "link_rtt_ms": "nominal_link_rtt_ms",
    "power_draw_w": "nominal_power_draw_w",
}

_PARAMETER_CATALOGS: dict[str, tuple[str, ...]] = {
    "power": POWER_PARAMETERS,
    "water": WATER_PARAMETERS,
    "gas": GAS_PARAMETERS,
    "communications": COMMUNICATIONS_PARAMETERS,
}

_COMM_CONDITION_RISK = {
    "healthy": "low",
    "warning": "medium",
    "high_risk": "high",
    "critical": "critical",
    "unavailable": "critical",
}


@dataclass(frozen=True)
class ParameterProjection:
    available: bool
    parameter: str
    baseline_value: float | None
    projected_value: float | None
    unit: str
    condition: str  # HEALTHY | STABLE | AT RISK | CRITICAL | UNAVAILABLE
    potential_alert: bool
    potential_risk: str
    explanation: str
    recommended_action: str
    rule_refs: list[str]


def list_supported_parameters(utility_kind: str, available_metrics: set[str]) -> list[dict[str, Any]]:
    kind = (utility_kind or "").lower()
    catalog = _PARAMETER_CATALOGS.get(kind, ())
    out: list[dict[str, Any]] = []
    for key in catalog:
        out.append(
            {
                "parameter": key,
                "available": key in available_metrics,
                "unit": _unit_for(key),
                "note": (
                    "Live baseline present — parameter what-if supported."
                    if key in available_metrics
                    else "Scenario unavailable for this parameter (no live baseline telemetry)."
                ),
            }
        )
    return out


def nominal_from_profile(parameter: str, profile: dict[str, Any] | None) -> float | None:
    """Resolve Gas/Communications profile nominal for relative-band parameters."""
    if not profile:
        return None
    key = GAS_NOMINAL_PROFILE_KEYS.get(parameter) or COMMUNICATIONS_NOMINAL_PROFILE_KEYS.get(
        parameter
    )
    if not key:
        return None
    raw = profile.get(key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if abs(value) < 1e-9:
        return None
    return value


def project_parameter(
    *,
    utility_kind: str,
    asset_type: str,
    parameter: str,
    baseline_value: float | None,
    delta_percent: float | None,
    absolute_value: float | None,
    nominal_value: float | None = None,
) -> ParameterProjection:
    kind = (utility_kind or "").lower()
    param = (parameter or "").strip()

    if kind == "power" and param not in POWER_PARAMETERS:
        return _unavailable(param, "Scenario unavailable for this parameter (not supported for Power).")
    if kind == "water" and param not in WATER_PARAMETERS:
        return _unavailable(param, "Scenario unavailable for this parameter (not supported for Water).")
    if kind == "gas" and param not in GAS_PARAMETERS:
        return _unavailable(param, "Scenario unavailable for this parameter (not supported for Gas).")
    if kind == "communications" and param not in COMMUNICATIONS_PARAMETERS:
        return _unavailable(
            param,
            "Scenario unavailable for this parameter (not supported for Communications).",
        )
    if kind not in {"water", "power", "gas", "communications"}:
        return _unavailable(param, "Scenario unavailable for this utility.")

    if baseline_value is None:
        return _unavailable(
            param,
            "Scenario unavailable for this parameter (no live baseline telemetry).",
        )

    if absolute_value is not None:
        projected = float(absolute_value)
        change_note = f"absolute value set to {projected}"
    elif delta_percent is not None:
        projected = float(baseline_value) * (1.0 + float(delta_percent) / 100.0)
        change_note = f"changed by {delta_percent:+.1f}%"
    else:
        return _unavailable(param, "Provide delta_percent or absolute_value.")

    projected = round(projected, 2)
    baseline = round(float(baseline_value), 2)

    if kind == "power":
        return _project_power(param, baseline, projected, change_note, asset_type)
    if kind == "gas":
        return _project_gas(
            param,
            baseline,
            projected,
            change_note,
            asset_type=asset_type,
            nominal_value=nominal_value,
        )
    if kind == "communications":
        return _project_communications(
            param,
            baseline,
            projected,
            change_note,
            asset_type=asset_type,
            nominal_value=nominal_value,
        )
    return _project_water(param, baseline, projected, change_note)


def _project_power(
    param: str,
    baseline: float,
    projected: float,
    change_note: str,
    asset_type: str,
) -> ParameterProjection:
    unit = _unit_for(param)
    condition = "HEALTHY"
    potential_alert = False
    risk = "low"
    refs: list[str] = []
    action = "Continue normal monitoring."

    if param == "load_pct":
        refs = [
            f"LOAD_WARN={power_health.LOAD_WARN}",
            f"LOAD_HIGH={power_health.LOAD_HIGH}",
            f"LOAD_CRITICAL={power_health.LOAD_CRITICAL}",
        ]
        if projected >= power_health.LOAD_CRITICAL:
            condition, potential_alert, risk = "CRITICAL", True, "critical"
            action = "Reduce load or shed non-critical demand; inspect transformer thermal margin."
        elif projected >= power_health.LOAD_HIGH:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Prepare load relief; verify cooling and oil temperature trends."
        elif projected >= power_health.LOAD_WARN:
            condition, potential_alert, risk = "STABLE", False, "medium"
            action = "Increase monitoring cadence on load and oil temperature."
        explanation = (
            f"Load {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Compared to Power health bands warn≥{power_health.LOAD_WARN}, "
            f"high≥{power_health.LOAD_HIGH}, critical≥{power_health.LOAD_CRITICAL}."
        )
    elif param == "oil_temp_c":
        refs = [
            f"OIL_WARN={power_health.OIL_WARN}",
            f"OIL_HIGH={power_health.OIL_HIGH}",
            f"OIL_CRITICAL={power_health.OIL_CRITICAL}",
        ]
        if projected >= power_health.OIL_CRITICAL:
            condition, potential_alert, risk = "CRITICAL", True, "critical"
            action = "Urgent thermal inspection; reduce load and verify cooling."
        elif projected >= power_health.OIL_HIGH:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Investigate cooling path; consider load reduction."
        elif projected >= power_health.OIL_WARN:
            condition, potential_alert, risk = "STABLE", False, "medium"
            action = "Monitor oil temperature trend closely."
        explanation = (
            f"Oil temperature {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Bands warn≥{power_health.OIL_WARN}, high≥{power_health.OIL_HIGH}, "
            f"critical≥{power_health.OIL_CRITICAL} (°C)."
        )
    else:  # voltage_kv
        refs = [
            f"HV_VOLTAGE_LOW={power_health.HV_VOLTAGE_LOW}",
            f"HV_VOLTAGE_CRITICAL={power_health.HV_VOLTAGE_CRITICAL}",
            f"MV_VOLTAGE_LOW={power_health.MV_VOLTAGE_LOW}",
            f"MV_VOLTAGE_CRITICAL={power_health.MV_VOLTAGE_CRITICAL}",
        ]
        hv = projected >= 50.0 or baseline >= 50.0
        low = power_health.HV_VOLTAGE_LOW if hv else power_health.MV_VOLTAGE_LOW
        crit = power_health.HV_VOLTAGE_CRITICAL if hv else power_health.MV_VOLTAGE_CRITICAL
        if projected <= crit:
            condition, potential_alert, risk = "CRITICAL", True, "critical"
            action = "Investigate undervoltage; check feeder and transformer taps."
        elif projected <= low:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Verify voltage regulation and downstream loading."
        explanation = (
            f"Voltage {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Using {'HV' if hv else 'MV'} bands low≤{low}, critical≤{crit} "
            f"(asset_type={asset_type or 'n/a'})."
        )

    return ParameterProjection(
        available=True,
        parameter=param,
        baseline_value=baseline,
        projected_value=projected,
        unit=unit,
        condition=condition,
        potential_alert=potential_alert,
        potential_risk=risk,
        explanation=explanation,
        recommended_action=action,
        rule_refs=refs,
    )


def _project_water(
    param: str,
    baseline: float,
    projected: float,
    change_note: str,
) -> ParameterProjection:
    unit = _unit_for(param)
    condition = "HEALTHY"
    potential_alert = False
    risk = "low"
    refs: list[str] = []
    action = "Continue normal monitoring."

    if param == "pressure":
        refs = [
            f"PRESSURE_CRITICAL_LOW={water_health.PRESSURE_CRITICAL_LOW}",
            f"PRESSURE_LOW={water_health.PRESSURE_LOW}",
            f"PRESSURE_HIGH={water_health.PRESSURE_HIGH}",
        ]
        if projected <= water_health.PRESSURE_CRITICAL_LOW:
            condition, potential_alert, risk = "CRITICAL", True, "critical"
            action = "Investigate pressure collapse; check pumps and isolation valves."
        elif projected < water_health.PRESSURE_LOW or projected > water_health.PRESSURE_HIGH:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Verify pump duty and network demand; inspect for leaks if low."
        elif projected < water_health.PRESSURE_IDEAL[0] or projected > water_health.PRESSURE_IDEAL[1]:
            condition, risk = "STABLE", "medium"
            action = "Tune toward ideal pressure band."
        explanation = (
            f"Pressure {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Critical low≤{water_health.PRESSURE_CRITICAL_LOW}, "
            f"low<{water_health.PRESSURE_LOW}, high>{water_health.PRESSURE_HIGH}."
        )
    elif param in {"tank_level", "reservoir_level"}:
        refs = [
            f"LEVEL_CRITICAL={water_health.LEVEL_CRITICAL}",
            f"LEVEL_LOW={water_health.LEVEL_LOW}",
        ]
        if projected <= water_health.LEVEL_CRITICAL:
            condition, potential_alert, risk = "CRITICAL", True, "critical"
            action = "Increase inflow / curtail demand; protect critical zones."
        elif projected < water_health.LEVEL_LOW:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Raise storage; review outflow schedule."
        explanation = (
            f"Level {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Critical≤{water_health.LEVEL_CRITICAL}, low<{water_health.LEVEL_LOW}."
        )
    elif param == "flow_rate":
        refs = [f"FLOW_LOW={water_health.FLOW_LOW}"]
        if projected < water_health.FLOW_LOW:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Check pump availability and network restrictions."
        elif projected > baseline * 1.35:
            condition, risk = "STABLE", "medium"
            action = "Validate demand surge vs pump capacity."
        explanation = (
            f"Flow {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Low-flow band <{water_health.FLOW_LOW}."
        )
    else:  # pump_speed
        refs = ["pump_speed operational heuristic (not a physics model)"]
        if projected <= 50:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Investigate pump duty cycle and standby capacity."
        explanation = (
            f"Pump speed {baseline}{unit} → {projected}{unit} ({change_note}). "
            "Heuristic only — not hydraulic affinity-law physics."
        )

    return ParameterProjection(
        available=True,
        parameter=param,
        baseline_value=baseline,
        projected_value=projected,
        unit=unit,
        condition=condition,
        potential_alert=potential_alert,
        potential_risk=risk,
        explanation=explanation,
        recommended_action=action,
        rule_refs=refs,
    )


def _project_gas(
    param: str,
    baseline: float,
    projected: float,
    change_note: str,
    *,
    asset_type: str,
    nominal_value: float | None,
) -> ParameterProjection:
    """
    Project Gas condition using the same bands as ``app.utilities.gas.health``.

    Absolute metrics: threshold comparisons on the projected value.
    Relative metrics (pressure/flow): |projected − nominal| / |nominal|
    against REL_DEV_WARN / HIGH / CRITICAL (identical to live Gas health).
    """
    unit = _unit_for(param)
    condition = "HEALTHY"
    potential_alert = False
    risk = "low"
    refs: list[str] = []
    action = "Continue normal monitoring."

    if param == "compressor_load_pct":
        refs = [
            f"LOAD_WARN={gas_health.LOAD_WARN}",
            f"LOAD_HIGH={gas_health.LOAD_HIGH}",
            f"LOAD_CRITICAL={gas_health.LOAD_CRITICAL}",
        ]
        if projected >= gas_health.LOAD_CRITICAL:
            condition, potential_alert, risk = "CRITICAL", True, "critical"
            action = "Reduce compressor duty or shed non-critical demand; inspect cooling and surge margin."
        elif projected >= gas_health.LOAD_HIGH:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Prepare load relief; verify vibration and discharge temperature trends."
        elif projected >= gas_health.LOAD_WARN:
            condition, risk = "STABLE", "medium"
            action = "Increase monitoring cadence on compressor load and vibration."
        explanation = (
            f"Compressor load {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Bands warn≥{gas_health.LOAD_WARN}, high≥{gas_health.LOAD_HIGH}, "
            f"critical≥{gas_health.LOAD_CRITICAL}."
        )
    elif param == "vibration_mm_s":
        refs = [
            f"VIBE_WARN={gas_health.VIBE_WARN}",
            f"VIBE_HIGH={gas_health.VIBE_HIGH}",
            f"VIBE_CRITICAL={gas_health.VIBE_CRITICAL}",
        ]
        if projected >= gas_health.VIBE_CRITICAL:
            condition, potential_alert, risk = "CRITICAL", True, "critical"
            action = "Immediate mechanical inspection; reduce load and check alignment/bearings."
        elif projected >= gas_health.VIBE_HIGH:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Investigate vibration source; schedule predictive maintenance check."
        elif projected >= gas_health.VIBE_WARN:
            condition, risk = "STABLE", "medium"
            action = "Watch vibration trend; confirm mounting and balance."
        explanation = (
            f"Vibration {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Bands warn≥{gas_health.VIBE_WARN}, high≥{gas_health.VIBE_HIGH}, "
            f"critical≥{gas_health.VIBE_CRITICAL} mm/s."
        )
    elif param == "temperature_c":
        refs = [
            f"TEMP_WARN={gas_health.TEMP_WARN}",
            f"TEMP_HIGH={gas_health.TEMP_HIGH}",
            f"TEMP_CRITICAL={gas_health.TEMP_CRITICAL}",
        ]
        if projected >= gas_health.TEMP_CRITICAL:
            condition, potential_alert, risk = "CRITICAL", True, "critical"
            action = "Urgent thermal inspection; reduce load and verify cooling path."
        elif projected >= gas_health.TEMP_HIGH:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Investigate process temperature rise; consider load reduction."
        elif projected >= gas_health.TEMP_WARN:
            condition, risk = "STABLE", "medium"
            action = "Monitor temperature trend closely."
        explanation = (
            f"Temperature {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Bands warn≥{gas_health.TEMP_WARN}, high≥{gas_health.TEMP_HIGH}, "
            f"critical≥{gas_health.TEMP_CRITICAL} °C."
        )
    elif param == "leak_indicator":
        refs = [
            f"LEAK_WARN={gas_health.LEAK_WARN}",
            f"LEAK_HIGH={gas_health.LEAK_HIGH}",
            f"LEAK_CRITICAL={gas_health.LEAK_CRITICAL}",
        ]
        if projected >= gas_health.LEAK_CRITICAL:
            condition, potential_alert, risk = "CRITICAL", True, "critical"
            action = "Treat as critical leak-indicator event (POC); isolate segment if operable."
        elif projected >= gas_health.LEAK_HIGH:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Increase leak survey cadence; verify seals and joint integrity."
        elif projected >= gas_health.LEAK_WARN:
            condition, risk = "STABLE", "medium"
            action = "Continue elevated monitoring of leak indicator (synthetic POC)."
        explanation = (
            f"Leak indicator {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Synthetic POC bands warn≥{gas_health.LEAK_WARN}, high≥{gas_health.LEAK_HIGH}, "
            f"critical≥{gas_health.LEAK_CRITICAL} (not certified CH₄)."
        )
    elif param == "gas_quality_pct":
        # Lower quality is worse (same ordering as gas.health evaluate_alerts).
        refs = [
            f"QUALITY_WARN={gas_health.QUALITY_WARN}",
            f"QUALITY_HIGH={gas_health.QUALITY_HIGH}",
            f"QUALITY_CRITICAL={gas_health.QUALITY_CRITICAL}",
        ]
        if projected <= gas_health.QUALITY_CRITICAL:
            condition, potential_alert, risk = "CRITICAL", True, "critical"
            action = "Hold downstream delivery pending quality confirmation; check upstream feedstock."
        elif projected <= gas_health.QUALITY_HIGH:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Increase quality sampling; investigate contamination or blending."
        elif projected <= gas_health.QUALITY_WARN:
            condition, risk = "STABLE", "medium"
            action = "Monitor gas quality trend; verify analyzer calibration."
        explanation = (
            f"Gas quality {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Degradation bands warn≤{gas_health.QUALITY_WARN}, high≤{gas_health.QUALITY_HIGH}, "
            f"critical≤{gas_health.QUALITY_CRITICAL}."
        )
    elif param == "valve_position_pct":
        refs = [
            f"VALVE_WARN_LOW={gas_health.VALVE_WARN_LOW}",
            f"VALVE_WARN_HIGH={gas_health.VALVE_WARN_HIGH}",
            f"VALVE_HIGH_LOW={gas_health.VALVE_HIGH_LOW}",
            f"VALVE_HIGH_HIGH={gas_health.VALVE_HIGH_HIGH}",
        ]
        if projected <= gas_health.VALVE_HIGH_LOW or projected >= gas_health.VALVE_HIGH_HIGH:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Verify regulator setpoints and actuator travel; avoid extreme valve positions."
        elif projected <= gas_health.VALVE_WARN_LOW or projected >= gas_health.VALVE_WARN_HIGH:
            condition, risk = "STABLE", "medium"
            action = "Watch valve travel; confirm demand vs setpoint."
        explanation = (
            f"Valve position {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Warn outside [{gas_health.VALVE_WARN_LOW}, {gas_health.VALVE_WARN_HIGH}]; "
            f"high outside [{gas_health.VALVE_HIGH_LOW}, {gas_health.VALVE_HIGH_HIGH}]."
        )
    elif param in {"pressure_bar", "flow_rate_nm3_h"}:
        # Same relative-deviation math as gas.health._rel_deviation.
        nominal = float(nominal_value) if nominal_value is not None else None
        if nominal is None or abs(nominal) < 1e-9:
            # Honest fallback: use live baseline as reference when profile nominal missing.
            nominal = baseline if abs(baseline) >= 1e-9 else None
            nominal_note = "profile nominal unavailable — using live baseline as reference"
        else:
            nominal_note = f"profile nominal={round(nominal, 2)}{unit}"

        if nominal is None:
            return _unavailable(
                param,
                "Scenario unavailable for this parameter (no nominal or usable baseline for relative band).",
            )

        deviation = abs(projected - nominal) / abs(nominal)
        refs = [
            f"REL_DEV_WARN={gas_health.REL_DEV_WARN}",
            f"REL_DEV_HIGH={gas_health.REL_DEV_HIGH}",
            f"REL_DEV_CRITICAL={gas_health.REL_DEV_CRITICAL}",
            f"nominal={round(nominal, 2)}",
            f"|Δ|/nominal={deviation:.4f}",
        ]
        label = "Pressure" if param == "pressure_bar" else "Flow"
        if deviation >= gas_health.REL_DEV_CRITICAL:
            condition, potential_alert, risk = "CRITICAL", True, "critical"
            action = (
                "Investigate pressure collapse / surge"
                if param == "pressure_bar"
                else "Investigate flow collapse / surge; check upstream supply and demand."
            )
        elif deviation >= gas_health.REL_DEV_HIGH:
            condition, potential_alert, risk = "AT RISK", True, "high"
            action = "Verify network demand and setpoints; inspect for leaks if pressure low."
        elif deviation >= gas_health.REL_DEV_WARN:
            condition, risk = "STABLE", "medium"
            action = f"Tune toward nominal {label.lower()}; continue monitoring."
        explanation = (
            f"{label} {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Relative deviation |projected−nominal|/|nominal| = {deviation:.3%} "
            f"({nominal_note}). Bands warn≥{gas_health.REL_DEV_WARN:.0%}, "
            f"high≥{gas_health.REL_DEV_HIGH:.0%}, critical≥{gas_health.REL_DEV_CRITICAL:.0%} "
            f"(asset_type={asset_type or 'n/a'})."
        )
    else:
        return _unavailable(param, "Scenario unavailable for this parameter (not supported for Gas).")

    return ParameterProjection(
        available=True,
        parameter=param,
        baseline_value=baseline,
        projected_value=projected,
        unit=unit,
        condition=condition,
        potential_alert=potential_alert,
        potential_risk=risk,
        explanation=explanation,
        recommended_action=action,
        rule_refs=refs,
    )


def _project_communications(
    param: str,
    baseline: float,
    projected: float,
    change_note: str,
    *,
    asset_type: str,
    nominal_value: float | None,
) -> ParameterProjection:
    """
    Project Communications condition using ``app.utilities.communications.health``.

    Single-parameter what-if: evaluate alerts for the projected metric only, then
    ``condition_from_alerts`` (same precedence as live health, including link_down).
    Condition vocabulary matches live Comm: healthy | warning | high_risk |
    critical | unavailable. Not a network physics model.
    """
    unit = _unit_for(param)
    nominals: dict[str, float] = {}

    if param == "link_rtt_ms":
        nominal = float(nominal_value) if nominal_value is not None else None
        if nominal is None or abs(nominal) < 1e-9:
            # Honest fallback when profile nominal missing — use live baseline.
            nominal = baseline if abs(baseline) >= 1e-9 else None
            nominal_note = "profile nominal unavailable — using live baseline as nominal"
        else:
            nominal_note = f"profile nominal={round(nominal, 2)}{unit}"
        if nominal is None:
            return _unavailable(
                param,
                "Scenario unavailable for this parameter "
                "(no nominal_link_rtt_ms or usable baseline).",
            )
        nominals["nominal_link_rtt_ms"] = float(nominal)
    elif param == "power_draw_w":
        nominal = float(nominal_value) if nominal_value is not None else None
        if nominal is None or abs(nominal) < 1e-9:
            nominal = baseline if abs(baseline) >= 1e-9 else None
            nominal_note = "profile nominal unavailable — using live baseline as nominal"
        else:
            nominal_note = f"profile nominal={round(nominal, 2)}{unit}"
        if nominal is None:
            return _unavailable(
                param,
                "Scenario unavailable for this parameter "
                "(no nominal_power_draw_w or usable baseline).",
            )
        nominals["nominal_power_draw_w"] = float(nominal)
    else:
        nominal_note = ""

    metrics: dict[str, float] = {param: float(projected)}
    # Single-parameter scenarios: when not projecting link_up, assume link remains up
    # so other metric bands are interpretable (matches Gas single-metric projections).
    if param != "link_up":
        metrics["link_up"] = 1.0

    alerts = communications_health.evaluate_alerts(
        metrics,
        asset_type=asset_type,
        nominals=nominals,
    )
    link_up_for_condition = (
        float(projected) if param == "link_up" else float(metrics["link_up"])
    )
    condition = communications_health.condition_from_alerts(
        alerts,
        has_telemetry=True,
        link_up=link_up_for_condition,
    )
    risk = _COMM_CONDITION_RISK.get(condition, "medium")
    potential_alert = condition in {"warning", "high_risk", "critical", "unavailable"}

    refs: list[str] = [f"condition={condition}", f"alerts={','.join(alerts) or 'none'}"]
    if param == "link_rtt_ms":
        refs.extend(
            [
                f"RTT_DELTA_WARN={communications_health.RTT_DELTA_WARN}",
                f"RTT_DELTA_HIGH={communications_health.RTT_DELTA_HIGH}",
                f"RTT_DELTA_CRITICAL={communications_health.RTT_DELTA_CRITICAL}",
            ]
        )
        action = {
            "unavailable": "Link path unavailable — restore link before interpreting RTT.",
            "critical": "Investigate path quality; check adjacent POP/gateway RTT in zone.",
            "high_risk": "Increase path monitoring; compare peer Communications assets.",
            "warning": "Watch RTT trend; verify no planned maintenance on fibre path.",
            "healthy": "Continue normal monitoring.",
        }.get(condition, "Continue normal monitoring.")
        explanation = (
            f"Link RTT {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Overage vs nominal uses Communications health bands "
            f"warn≥{communications_health.RTT_DELTA_WARN} ms, "
            f"high≥{communications_health.RTT_DELTA_HIGH} ms, "
            f"critical≥{communications_health.RTT_DELTA_CRITICAL} ms "
            f"({nominal_note}; asset_type={asset_type or 'n/a'})."
        )
    elif param == "packet_loss_pct":
        refs.extend(
            [
                f"LOSS_WARN={communications_health.LOSS_WARN}",
                f"LOSS_HIGH={communications_health.LOSS_HIGH}",
                f"LOSS_CRITICAL={communications_health.LOSS_CRITICAL}",
            ]
        )
        action = {
            "unavailable": "Link unavailable — restore link before interpreting loss.",
            "critical": "Treat as severe path loss; isolate segment if operable.",
            "high_risk": "Investigate packet loss source; check peer gateways.",
            "warning": "Increase monitoring cadence on packet loss.",
            "healthy": "Continue normal monitoring.",
        }.get(condition, "Continue normal monitoring.")
        explanation = (
            f"Packet loss {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Absolute POC bands warn≥{communications_health.LOSS_WARN}%, "
            f"high≥{communications_health.LOSS_HIGH}%, "
            f"critical≥{communications_health.LOSS_CRITICAL}%."
        )
    elif param == "link_up":
        refs.append(f"LINK_DOWN={communications_health.LINK_DOWN}")
        action = (
            "Link projected down (link_up < LINK_DOWN) — restore physical/logical path; "
            "RTT/loss are not interpretable while link is down."
            if condition == "unavailable"
            else "Continue normal monitoring of link availability."
        )
        explanation = (
            f"Link up {baseline} → {projected} ({change_note}). "
            f"Communications health: link_up < {communications_health.LINK_DOWN} ⇒ "
            f"condition unavailable (dominates other severities)."
        )
    elif param == "cabinet_temp_c":
        refs.extend(
            [
                f"TEMP_WARN={communications_health.TEMP_WARN}",
                f"TEMP_HIGH={communications_health.TEMP_HIGH}",
                f"TEMP_CRITICAL={communications_health.TEMP_CRITICAL}",
            ]
        )
        action = {
            "unavailable": "Link unavailable — restore link; still inspect cabinet cooling.",
            "critical": "Urgent thermal inspection of edge cabinet; verify cooling.",
            "high_risk": "Investigate cabinet temperature rise; check ventilation.",
            "warning": "Monitor cabinet temperature trend closely.",
            "healthy": "Continue normal monitoring.",
        }.get(condition, "Continue normal monitoring.")
        explanation = (
            f"Cabinet temperature {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Communications cabinet bands (not Gas/NEBS) warn≥{communications_health.TEMP_WARN}, "
            f"high≥{communications_health.TEMP_HIGH}, "
            f"critical≥{communications_health.TEMP_CRITICAL} °C."
        )
    elif param == "power_draw_w":
        refs.extend(
            [
                f"POWER_REL_WARN={communications_health.POWER_REL_WARN}",
                f"POWER_REL_HIGH={communications_health.POWER_REL_HIGH}",
            ]
        )
        action = {
            "unavailable": "Link unavailable — restore link; verify power feed separately.",
            "critical": "Investigate power anomaly on edge cabinet supply.",
            "high_risk": "Verify power draw vs nominal; inspect PSU/load.",
            "warning": "Watch power draw deviation from nominal.",
            "healthy": "Continue normal monitoring.",
        }.get(condition, "Continue normal monitoring.")
        explanation = (
            f"Power draw {baseline}{unit} → {projected}{unit} ({change_note}). "
            f"Secondary relative deviation vs nominal ({nominal_note}); "
            f"warn≥{communications_health.POWER_REL_WARN:.0%}, "
            f"high≥{communications_health.POWER_REL_HIGH:.0%} "
            f"(no power_draw_critical code — same as live Communications health)."
        )
    else:
        return _unavailable(
            param, "Scenario unavailable for this parameter (not supported for Communications)."
        )

    return ParameterProjection(
        available=True,
        parameter=param,
        baseline_value=baseline,
        projected_value=projected,
        unit=unit,
        condition=condition,
        potential_alert=potential_alert,
        potential_risk=risk,
        explanation=explanation,
        recommended_action=action,
        rule_refs=refs,
    )


def _unavailable(param: str, reason: str) -> ParameterProjection:
    return ParameterProjection(
        available=False,
        parameter=param,
        baseline_value=None,
        projected_value=None,
        unit=_unit_for(param),
        condition="UNAVAILABLE",
        potential_alert=False,
        potential_risk="none",
        explanation=reason,
        recommended_action="Choose a supported parameter with live baseline telemetry.",
        rule_refs=[],
    )


def _unit_for(param: str) -> str:
    return {
        "load_pct": "%",
        "voltage_kv": "kV",
        "oil_temp_c": "°C",
        "pressure": "bar",
        "tank_level": "%",
        "reservoir_level": "%",
        "flow_rate": "m³/h",
        "pump_speed": "rpm",
        "compressor_load_pct": "%",
        "vibration_mm_s": "mm/s",
        "temperature_c": "°C",
        "leak_indicator": "0-1",
        "gas_quality_pct": "%",
        "pressure_bar": "bar",
        "flow_rate_nm3_h": "Nm³/h",
        "valve_position_pct": "%",
        "link_rtt_ms": "ms",
        "packet_loss_pct": "%",
        "link_up": "0/1",
        "cabinet_temp_c": "°C",
        "power_draw_w": "W",
    }.get(param, "")
