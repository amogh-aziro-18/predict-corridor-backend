"""
Corridor resilience diagnostic (Phase 14.14).

Composes existing DashboardService + ResilienceAssessmentService + alerts +
work orders + asset registry into an operator-facing diagnostic.

Does NOT invent a numeric resilience score or a second health model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.api.schemas.resilience_diagnostic import (
    DiagnosticSliceOut,
    ResilienceDiagnosticOut,
)
from app.domain.enums import AlertStatus, WorkOrderStatus
from app.domain.repositories import AlertRepository, AssetRepository, WorkOrderRepository
from app.services.dashboard_service import DashboardService
from app.services.resilience_assessment_service import ResilienceAssessmentService

_NOTE = (
    "Corridor diagnostic composed from live Water/Power/Gas/Communications dashboards, "
    "weather and environment impact assessments, open alerts, active work orders, and "
    "registry predictive fields. Categorical postures only — not a 0–100 resilience score."
)


def _elevated_registry_risk(assets: list[Any]) -> list[Any]:
    """Registry risk / predicted-failure evidence — not a new ML run."""
    out: list[Any] = []
    for a in assets:
        if (a.risk or "").lower() in {"high", "critical"}:
            out.append(a)
            continue
        if a.predicted_failure_days is not None and float(a.predicted_failure_days) <= 14:
            out.append(a)
    return out

# Map dashboard DEGRADED → operator AT RISK (Phase 14.14 vocabulary).
_DASHBOARD_TO_OPERATOR = {
    "HEALTHY": "HEALTHY",
    "STABLE": "STABLE",
    "DEGRADED": "AT RISK",
    "CRITICAL": "CRITICAL",
    "UNAVAILABLE": "UNAVAILABLE",
}

_RESILIENCE_TO_OPERATOR = {
    "none": "HEALTHY",
    "low": "STABLE",
    "medium": "AT RISK",
    "high": "AT RISK",
    "critical": "CRITICAL",
}

_POSTURE_RANK = {
    "HEALTHY": 0,
    "STABLE": 1,
    "AT RISK": 2,
    "CRITICAL": 3,
    "UNAVAILABLE": -1,
}


def _worst(*postures: str) -> str:
    usable = [p for p in postures if p in _POSTURE_RANK and p != "UNAVAILABLE"]
    if not usable:
        return "UNAVAILABLE" if any(p == "UNAVAILABLE" for p in postures) else "STABLE"
    return max(usable, key=lambda p: _POSTURE_RANK.get(p, 0))


def _map_dashboard(posture: str) -> str:
    return _DASHBOARD_TO_OPERATOR.get((posture or "").upper(), "STABLE")


def _map_resilience(posture: str) -> str:
    return _RESILIENCE_TO_OPERATOR.get((posture or "").lower(), "STABLE")


class ResilienceDiagnosticService:
    """Compose corridor diagnostic from existing backend state."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._dashboard = DashboardService(db)
        self._resilience = ResilienceAssessmentService(db)
        self._alerts = AlertRepository(db)
        self._work_orders = WorkOrderRepository(db)
        self._assets = AssetRepository(db)

    def get_diagnostic(self, *, hours: int = 24) -> ResilienceDiagnosticOut:
        now = datetime.now(timezone.utc)
        hours = max(1, min(int(hours), 168))

        corridor = self._dashboard.corridor_dashboard()
        resilience_ctx = self._resilience.get_context(hours=hours)

        water_util = next((u for u in corridor.utilities if u.utility == "water"), None)
        power_util = next((u for u in corridor.utilities if u.utility == "power"), None)
        gas_util = next((u for u in corridor.utilities if u.utility == "gas"), None)
        communications_util = next(
            (u for u in corridor.utilities if u.utility == "communications"),
            None,
        )

        active_alerts = [
            a
            for a in self._alerts.list(active_only=True)
            if a.status in {AlertStatus.OPEN.value, AlertStatus.ACKNOWLEDGED.value}
        ]
        critical_high_alerts = [
            a for a in active_alerts if (a.severity or "").lower() in {"critical", "high"}
        ]
        active_wos = [
            wo
            for wo in self._work_orders.list()
            if wo.status
            in {
                WorkOrderStatus.OPEN.value,
                WorkOrderStatus.ASSIGNED.value,
                WorkOrderStatus.IN_PROGRESS.value,
            }
        ]

        water_assets = self._assets.list(utility_kind="water")
        power_assets = self._assets.list(utility_kind="power")
        gas_assets = self._assets.list(utility_kind="gas")
        communications_assets = self._assets.list(utility_kind="communications")
        water_elevated_pdm = _elevated_registry_risk(water_assets)
        power_elevated_pdm = _elevated_registry_risk(power_assets)
        gas_elevated_risk = _elevated_registry_risk(gas_assets)
        communications_elevated_risk = _elevated_registry_risk(communications_assets)

        weather_driver = next(
            (d for d in resilience_ctx.corridor.drivers if str(d.source) == "weather"),
            None,
        )
        env_driver = next(
            (d for d in resilience_ctx.corridor.drivers if str(d.source) == "environment"),
            None,
        )

        weather_posture = _map_resilience(
            weather_driver.severity if weather_driver else resilience_ctx.corridor.overall_posture
        )
        env_posture = _map_resilience(
            env_driver.severity if env_driver else "none"
        )
        operational_posture = _map_dashboard(corridor.overall_posture)

        water_posture = _map_dashboard(water_util.posture if water_util else "UNAVAILABLE")
        power_posture = _map_dashboard(power_util.posture if power_util else "UNAVAILABLE")
        gas_posture = _map_dashboard(gas_util.posture if gas_util else "UNAVAILABLE")
        communications_posture = _map_dashboard(
            communications_util.posture if communications_util else "UNAVAILABLE"
        )

        # Predictive note may elevate Power slice presentation without inventing ML.
        power_slice_posture = power_posture
        if power_elevated_pdm and _POSTURE_RANK.get(power_posture, 0) < _POSTURE_RANK["AT RISK"]:
            power_slice_posture = "AT RISK"

        # Registry risk may elevate Gas/Comm slice presentation — no new scoring model.
        gas_slice_posture = gas_posture
        if gas_elevated_risk and _POSTURE_RANK.get(gas_posture, 0) < _POSTURE_RANK["AT RISK"]:
            gas_slice_posture = "AT RISK"
        communications_slice_posture = communications_posture
        if (
            communications_elevated_risk
            and _POSTURE_RANK.get(communications_posture, 0) < _POSTURE_RANK["AT RISK"]
        ):
            communications_slice_posture = "AT RISK"

        overall = _worst(
            operational_posture,
            power_slice_posture,
            water_posture,
            gas_slice_posture,
            communications_slice_posture,
            weather_posture,
            env_posture,
        )

        slices: list[DiagnosticSliceOut] = [
            DiagnosticSliceOut(
                id="water",
                label="Water",
                posture=water_posture,
                kind="operational",
                summary=(
                    f"Live Water posture {water_posture} from dashboard "
                    f"(health {water_util.overall_health:.1f}%, "
                    f"{water_util.active_alerts} active alerts)."
                    if water_util and water_util.available
                    else "Water utility unavailable."
                ),
                metrics={
                    "health": water_util.overall_health if water_util else None,
                    "active_alerts": water_util.active_alerts if water_util else 0,
                    "critical_assets": water_util.critical_assets if water_util else 0,
                    "asset_count": water_util.asset_count if water_util else 0,
                    "elevated_maintenance_risk_assets": len(water_elevated_pdm),
                },
                evidence=[
                    f"{len(water_elevated_pdm)} Water asset(s) with elevated registry maintenance risk"
                    if water_elevated_pdm
                    else "No Water assets with elevated registry maintenance risk"
                ],
            ),
            DiagnosticSliceOut(
                id="power",
                label="Power",
                posture=power_slice_posture,
                kind="operational",
                summary=(
                    f"Live Power posture {power_slice_posture} "
                    f"(condition {power_util.overall_condition}, "
                    f"health {power_util.overall_health:.1f}%, "
                    f"{power_util.active_alerts} active alerts)."
                    if power_util and power_util.available
                    else "Power utility unavailable."
                ),
                metrics={
                    "health": power_util.overall_health if power_util else None,
                    "condition": power_util.overall_condition if power_util else None,
                    "active_alerts": power_util.active_alerts if power_util else 0,
                    "critical_assets": power_util.critical_assets if power_util else 0,
                    "asset_count": power_util.asset_count if power_util else 0,
                    "elevated_maintenance_risk_assets": len(power_elevated_pdm),
                },
                evidence=[
                    (
                        f"{len(power_elevated_pdm)} Power asset(s) currently have elevated "
                        "predictive/registry maintenance risk"
                    )
                    if power_elevated_pdm
                    else "No Power assets with elevated registry maintenance risk"
                ],
            ),
            DiagnosticSliceOut(
                id="gas",
                label="Gas",
                posture=gas_slice_posture,
                kind="operational",
                summary=(
                    f"Live Gas posture {gas_slice_posture} "
                    f"(condition {gas_util.overall_condition or 'unknown'}, "
                    f"health {(gas_util.overall_health or 0.0):.1f}%, "
                    f"{gas_util.active_alerts} active alerts)."
                    if gas_util and gas_util.available
                    else "Gas utility unavailable."
                ),
                metrics={
                    "health": gas_util.overall_health if gas_util else None,
                    "condition": gas_util.overall_condition if gas_util else None,
                    "active_alerts": gas_util.active_alerts if gas_util else 0,
                    "critical_assets": gas_util.critical_assets if gas_util else 0,
                    "asset_count": gas_util.asset_count if gas_util else 0,
                    "elevated_registry_risk_assets": len(gas_elevated_risk),
                },
                evidence=[
                    (
                        f"{len(gas_elevated_risk)} Gas asset(s) with elevated registry risk"
                    )
                    if gas_elevated_risk
                    else "No Gas assets with elevated registry risk"
                ],
            ),
            DiagnosticSliceOut(
                id="communications",
                label="Communications",
                posture=communications_slice_posture,
                kind="operational",
                summary=(
                    f"Live Communications posture {communications_slice_posture} "
                    f"(condition {communications_util.overall_condition or 'unknown'}, "
                    f"health {(communications_util.overall_health or 0.0):.1f}%, "
                    f"{communications_util.active_alerts} active alerts, "
                    f"{communications_util.asset_count} assets)."
                    if communications_util and communications_util.available
                    else "Communications utility unavailable."
                ),
                metrics={
                    "health": (
                        communications_util.overall_health if communications_util else None
                    ),
                    "condition": (
                        communications_util.overall_condition if communications_util else None
                    ),
                    "active_alerts": (
                        communications_util.active_alerts if communications_util else 0
                    ),
                    "critical_assets": (
                        communications_util.critical_assets if communications_util else 0
                    ),
                    "asset_count": (
                        communications_util.asset_count if communications_util else 0
                    ),
                    "elevated_registry_risk_assets": len(communications_elevated_risk),
                },
                evidence=[
                    (
                        f"{len(communications_elevated_risk)} Communications asset(s) with "
                        "elevated registry risk"
                    )
                    if communications_elevated_risk
                    else "No Communications assets with elevated registry risk",
                    "Communications has no ML/forecast model in this phase — "
                    "condition and alerts are rule-based from live telemetry.",
                ],
            ),
            DiagnosticSliceOut(
                id="environment",
                label="Environment",
                posture=env_posture,
                kind="environmental",
                summary=(
                    env_driver.explanation
                    if env_driver
                    else "Environment impact assessment unavailable or reporting none."
                ),
                metrics={
                    "raw_severity": env_driver.severity if env_driver else None,
                    "available": resilience_ctx.corridor.inputs_available.environment_available,
                },
                evidence=[env_driver.explanation] if env_driver else [],
            ),
            DiagnosticSliceOut(
                id="weather",
                label="Weather",
                posture=weather_posture,
                kind="weather",
                summary=(
                    weather_driver.explanation
                    if weather_driver
                    else "Weather impact assessment unavailable or reporting none."
                ),
                metrics={
                    "raw_severity": weather_driver.severity if weather_driver else None,
                    "available": resilience_ctx.corridor.inputs_available.weather_available,
                    "forecast_hours": hours,
                },
                evidence=[weather_driver.explanation] if weather_driver else [],
            ),
            DiagnosticSliceOut(
                id="operations",
                label="Operations",
                posture=self._operations_posture(critical_high_alerts, active_wos),
                kind="operations",
                summary=(
                    f"{len(critical_high_alerts)} critical/high open alerts · "
                    f"{len(active_wos)} active work orders"
                ),
                metrics={
                    "critical_high_alerts": len(critical_high_alerts),
                    "open_alerts_total": len(active_alerts),
                    "active_work_orders": len(active_wos),
                },
                evidence=[
                    f"Alert {a.id}: {a.severity} · {a.title}"
                    for a in critical_high_alerts[:5]
                ]
                + [
                    f"WO {wo.id}: {wo.status} · {wo.title}"
                    for wo in active_wos[:5]
                ],
            ),
        ]

        # Phase 16.5 — dependency exposure from resilience assessment (evidence only).
        dep_driver = next(
            (
                d
                for d in resilience_ctx.corridor.drivers
                if str(d.source) == "dependency_exposure"
            ),
            None,
        )
        if dep_driver is not None:
            dep_ev = dep_driver.evidence if isinstance(dep_driver.evidence, dict) else {}
            exposures = list(dep_ev.get("exposures") or [])
            dep_evidence: list[str] = []
            for exp in exposures[:5]:
                src = str(exp.get("source_asset_id") or "")
                sutil = str(exp.get("source_utility") or "")
                scond = str(exp.get("condition") or "")
                for row in list(exp.get("affected_assets") or [])[:4]:
                    dep_evidence.append(
                        f"Source {src} ({sutil}, {scond}) → "
                        f"{row.get('asset_id')} ({row.get('utility')}/{row.get('zone')}) "
                        "via supplies — potential downstream exposure"
                    )
            if not dep_evidence and dep_driver.explanation:
                dep_evidence = [dep_driver.explanation]
            slices.append(
                DiagnosticSliceOut(
                    id="dependency_exposure",
                    label="Dependency exposure",
                    # Informational — does not drive corridor overall_posture math.
                    posture="STABLE",
                    kind="operations",
                    summary=(
                        f"{int(dep_ev.get('exposure_count') or len(exposures))} critical/unavailable "
                        f"source(s) with modeled cross-utility downstream exposure. "
                        "Hypothetical only — does not elevate corridor resilience posture."
                    ),
                    metrics={
                        "exposure_count": dep_ev.get("exposure_count"),
                        "source_asset_ids": dep_ev.get("source_asset_ids") or [],
                        "affected_utilities": dep_ev.get("affected_utilities") or [],
                        "affected_zones": dep_ev.get("affected_zones") or [],
                        "affected_asset_ids": dep_ev.get("affected_asset_ids") or [],
                        "hypothetical": True,
                        "contributes_to_overall_posture": False,
                    },
                    evidence=dep_evidence,
                )
            )

        why_bullets = self._why_bullets(
            overall=overall,
            water_posture=water_posture,
            power_posture=power_slice_posture,
            gas_posture=gas_slice_posture,
            communications_posture=communications_slice_posture,
            weather_posture=weather_posture,
            env_posture=env_posture,
            power_elevated=len(power_elevated_pdm),
            water_elevated=len(water_elevated_pdm),
            gas_elevated=len(gas_elevated_risk),
            communications_elevated=len(communications_elevated_risk),
            critical_high_alerts=len(critical_high_alerts),
            active_wos=len(active_wos),
            corridor_drivers=list(corridor.drivers),
            dependency_explanation=dep_driver.explanation if dep_driver else None,
        )
        why = (
            f"The corridor is in {overall} posture. " + " ".join(why_bullets[:2])
            if why_bullets
            else (
                f"The corridor is in {overall} posture based on live Water, Power, Gas, "
                "and Communications signals."
            )
        )

        predictive_notes: list[str] = []
        if power_elevated_pdm:
            predictive_notes.append(
                f"Power predictive/registry risk elevated on "
                f"{len(power_elevated_pdm)} asset(s) "
                f"({', '.join(a.id for a in power_elevated_pdm[:4])}"
                f"{'…' if len(power_elevated_pdm) > 4 else ''}). "
                "GIS/Command Center show live PdM separately — diagnostic does not re-run ML."
            )
        if water_elevated_pdm:
            predictive_notes.append(
                f"Water registry maintenance risk elevated on {len(water_elevated_pdm)} asset(s)."
            )
        if gas_elevated_risk:
            predictive_notes.append(
                f"Gas registry risk elevated on {len(gas_elevated_risk)} asset(s)."
            )
        if communications_elevated_risk:
            predictive_notes.append(
                f"Communications registry risk elevated on "
                f"{len(communications_elevated_risk)} asset(s)."
            )

        return ResilienceDiagnosticOut(
            overall_posture=overall,
            why=why,
            why_bullets=why_bullets,
            slices=slices,
            environmental_context_posture=env_posture,
            weather_context_posture=weather_posture,
            operational_posture=operational_posture,
            predictive_notes=predictive_notes,
            assessed_at=now,
            note=_NOTE,
        )

    @staticmethod
    def _operations_posture(critical_high: list[Any], active_wos: list[Any]) -> str:
        crit = sum(1 for a in critical_high if (a.severity or "").lower() == "critical")
        if crit >= 1:
            return "CRITICAL"
        if len(critical_high) >= 2 or (critical_high and active_wos):
            return "AT RISK"
        if critical_high or active_wos:
            return "STABLE"
        return "HEALTHY"

    @staticmethod
    def _why_bullets(
        *,
        overall: str,
        water_posture: str,
        power_posture: str,
        gas_posture: str,
        communications_posture: str,
        weather_posture: str,
        env_posture: str,
        power_elevated: int,
        water_elevated: int,
        gas_elevated: int,
        communications_elevated: int,
        critical_high_alerts: int,
        active_wos: int,
        corridor_drivers: list[str],
        dependency_explanation: str | None = None,
    ) -> list[str]:
        bullets: list[str] = []
        bullets.extend(corridor_drivers[:4])
        if dependency_explanation:
            bullets.append(dependency_explanation)
        if power_elevated:
            bullets.append(
                f"Power resilience is reduced because {power_elevated} asset(s) "
                "currently have elevated predictive/registry maintenance risk."
            )
        if water_elevated:
            bullets.append(
                f"Water shows elevated maintenance risk on {water_elevated} asset(s)."
            )
        if gas_elevated:
            bullets.append(
                f"Gas shows elevated registry risk on {gas_elevated} asset(s)."
            )
        if communications_elevated:
            bullets.append(
                f"Communications shows elevated registry risk on "
                f"{communications_elevated} asset(s)."
            )
        for label, posture in (
            ("Water", water_posture),
            ("Power", power_posture),
            ("Gas", gas_posture),
            ("Communications", communications_posture),
        ):
            if posture in {"AT RISK", "CRITICAL", "UNAVAILABLE"}:
                bullets.append(f"{label} operational posture is {posture}.")
        if critical_high_alerts:
            bullets.append(
                f"{critical_high_alerts} critical/high alert(s) are currently open."
            )
        if active_wos:
            bullets.append(f"{active_wos} work order(s) are active in the corridor.")
        if weather_posture in {"AT RISK", "CRITICAL"}:
            bullets.append(f"Weather context posture is {weather_posture}.")
        if env_posture in {"AT RISK", "CRITICAL"}:
            bullets.append(f"Environmental context posture is {env_posture}.")
        if overall == "HEALTHY" and not bullets:
            bullets.append(
                "Water, Power, Gas, and Communications operational signals, weather, "
                "and environment are within healthy thresholds."
            )
        # Deduplicate while preserving order
        seen: set[str] = set()
        out: list[str] = []
        for b in bullets:
            if b not in seen:
                seen.add(b)
                out.append(b)
        return out
