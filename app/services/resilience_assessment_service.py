"""
Resilience Assessment Service (Phase 10A + 16.5 dependency evidence).

Composes WeatherImpactService + EnvironmentImpactService + scenario state +
static asset posture into corridor/zone resilience posture.

Phase 16.5 adds an optional ``dependency_exposure`` driver from
CrossUtilityImpactService for live critical/unavailable sources with outgoing
supplies edges. That driver is explanatory evidence only — it is NOT included
in ``_max_severity`` and does NOT elevate overall posture.

POC operational posture model — not a scientifically validated resilience index.
Does not reimplement weather/AQI thresholds or invent 0–100 scores.

Future extension: utility-neutral health adapters may elevate posture from
live telemetry; Water-specific water_health logic is intentionally omitted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.api.schemas.resilience import (
    ResilienceAssessmentOut,
    ResilienceContextOut,
    ResilienceDriverOut,
    ResilienceDriverSource,
    ResilienceInputsAvailabilityOut,
    ResilienceScope,
)
from app.core.config import Settings, get_settings
from app.core.errors import EnvironmentUnavailableError, WeatherUnavailableError
from app.domain.repositories import AssetRepository
from app.domain.repositories.scenario_repository import ScenarioRepository
from app.domain.repositories.zone_repository import ZoneRepository
from app.scenarios.definitions import ScenarioKey, get_scenario
from app.scenarios.resolution import resolve_effective_scenario
from app.services.environment_impact_service import EnvironmentImpactService
from app.services.weather_impact_service import WeatherImpactService

_SEVERITY_RANK = {
    "none": 0,
    "info": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "critical": 5,
}

_RANK_TO_SEVERITY = {v: k for k, v in _SEVERITY_RANK.items() if k != "info"}

_POC_NOTE = (
    "POC operational posture model composed from weather/environment impact "
    "assessments, scenario state, and static asset catalog posture. "
    "Not a scientifically validated resilience index and not a 0–100 score."
)

# Documented intensity elevation (minimal, deterministic):
# non-normal scenario with intensity >= 0.85 may rise one step (capped at critical).
_SCENARIO_INTENSITY_ELEVATION = 0.85

# Meaningful no-backup critical population for one-step elevation.
_NO_BACKUP_CRITICAL_ELEVATION_COUNT = 2


class ResilienceAssessmentService:
    """Corridor/zone resilience posture from existing impact + operational inputs."""

    def __init__(
        self,
        db: Session,
        *,
        weather_impact_service: WeatherImpactService | None = None,
        environment_impact_service: EnvironmentImpactService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._weather = weather_impact_service or WeatherImpactService(db)
        self._environment = environment_impact_service or EnvironmentImpactService(db)
        self._scenarios = ScenarioRepository(db)
        self._zones = ZoneRepository(db)
        self._assets = AssetRepository(db)

    def get_context(
        self,
        *,
        utility_kind: str | None = None,
        hours: int = 24,
    ) -> ResilienceContextOut:
        kind = self._resolve_utility_kind(utility_kind)
        hours = max(1, min(int(hours), 168))
        now = datetime.now(timezone.utc)

        corridor = self.assess(zone_id=None, utility_kind=kind, hours=hours, assessed_at=now)
        zones: list[ResilienceAssessmentOut] = []
        for zone in self._zones.list():
            zones.append(
                self.assess(
                    zone_id=zone.id,
                    utility_kind=kind,
                    hours=hours,
                    assessed_at=now,
                )
            )

        return ResilienceContextOut(
            utility_kind=kind,
            assessed_at=now,
            forecast_hours=hours,
            corridor=corridor,
            zones=zones,
            note=_POC_NOTE,
        )

    def assess(
        self,
        *,
        zone_id: str | None = None,
        utility_kind: str | None = None,
        hours: int = 24,
        assessed_at: datetime | None = None,
    ) -> ResilienceAssessmentOut:
        kind = self._resolve_utility_kind(utility_kind)
        hours = max(1, min(int(hours), 168))
        now = assessed_at or datetime.now(timezone.utc)
        scope = ResilienceScope.ZONE if zone_id else ResilienceScope.CORRIDOR

        inputs = ResilienceInputsAvailabilityOut()
        inputs_stale = False
        drivers: list[ResilienceDriverOut] = []
        evidence: list[str] = []

        weather_severity = "none"
        weather_driver = self._weather_driver(zone_id=zone_id, hours=hours)
        if weather_driver is None:
            inputs.weather_available = False
            evidence.append("Weather impact unavailable for this scope.")
        else:
            inputs.weather_available = True
            weather_severity = weather_driver.severity
            if weather_driver.evidence.get("stale"):
                inputs_stale = True
            drivers.append(weather_driver)
            evidence.append(weather_driver.explanation)

        env_severity = "none"
        env_driver = self._environment_driver(zone_id=zone_id, hours=hours)
        if env_driver is None:
            inputs.environment_available = False
            evidence.append("Environment impact unavailable for this scope.")
        else:
            inputs.environment_available = True
            env_severity = env_driver.severity
            if env_driver.evidence.get("stale"):
                inputs_stale = True
            drivers.append(env_driver)
            evidence.append(env_driver.explanation)

        scenario_driver = self._scenario_driver(zone_id=zone_id)
        inputs.scenario_available = True
        scenario_severity = scenario_driver.severity
        drivers.append(scenario_driver)
        evidence.append(scenario_driver.explanation)

        asset_driver, asset_stats = self._asset_posture_driver(
            zone_id=zone_id,
            utility_kind=kind,
        )
        inputs.assets_available = asset_stats["asset_count"] > 0
        drivers.append(asset_driver)
        evidence.append(asset_driver.explanation)

        # Phase 16.5 — dependency exposure evidence (NOT in _max_severity / elevation).
        dependency_driver = self._dependency_exposure_driver(zone_id=zone_id)
        inputs.dependencies_available = True
        if dependency_driver is not None:
            drivers.append(dependency_driver)
            evidence.append(dependency_driver.explanation)

        base = self._max_severity(weather_severity, env_severity, scenario_severity)
        elevated = False
        posture = base
        if (
            self._rank(base) >= self._rank("medium")
            and asset_stats["high_criticality_no_backup"] >= _NO_BACKUP_CRITICAL_ELEVATION_COUNT
        ):
            posture = self._elevate_one(base)
            elevated = posture != base
            if elevated:
                evidence.append(
                    "Posture elevated one step: external stress ≥ medium and "
                    f"{asset_stats['high_criticality_no_backup']} high/very-high "
                    "criticality assets have backup=none "
                    f"(threshold {_NO_BACKUP_CRITICAL_ELEVATION_COUNT})."
                )

        note_parts = [_POC_NOTE]
        if inputs_stale:
            note_parts.append("One or more weather/environment inputs are stale.")
        if (
            not inputs.weather_available
            and not inputs.environment_available
            and scenario_severity == "none"
            and asset_stats["asset_count"] == 0
        ):
            posture = "none"
            note_parts.append("Insufficient inputs to establish a meaningful resilience posture.")

        return ResilienceAssessmentOut(
            scope=scope,
            zone_id=zone_id,
            utility_kind=kind,
            overall_posture=posture,
            overall_severity=posture,
            drivers=drivers,
            evidence=evidence,
            assessed_at=now,
            forecast_hours=hours,
            inputs_available=inputs,
            inputs_stale=inputs_stale,
            note=" ".join(note_parts),
        )

    def _resolve_utility_kind(self, utility_kind: str | None) -> str:
        if utility_kind and utility_kind.strip():
            return utility_kind.strip().lower()
        kinds = self._settings.enabled_utility_kinds
        return kinds[0] if kinds else "water"

    def _weather_driver(
        self,
        *,
        zone_id: str | None,
        hours: int,
    ) -> ResilienceDriverOut | None:
        try:
            assessment = self._weather.assess(zone_id=zone_id, hours=hours)
        except WeatherUnavailableError:
            return None

        stale = bool(assessment.provider_meta.stale or assessment.forecast_provider_meta.stale)
        signal_summary = [
            {
                "signal_type": s.signal_type.value
                if hasattr(s.signal_type, "value")
                else str(s.signal_type),
                "severity": s.severity,
                "observation_kind": s.observation_kind.value
                if hasattr(s.observation_kind, "value")
                else str(s.observation_kind),
            }
            for s in assessment.signals[:8]
        ]
        severity = self._normalize_severity(assessment.overall_severity)
        kinds = sorted(
            {
                (
                    s.observation_kind.value
                    if hasattr(s.observation_kind, "value")
                    else str(s.observation_kind)
                )
                for s in assessment.signals
            }
        )
        explanation = (
            f"Weather impact overall severity {severity}"
            + (f" ({', '.join(kinds)})" if kinds else "")
            + ("; input stale" if stale else "")
            + "."
        )
        return ResilienceDriverOut(
            source=ResilienceDriverSource.WEATHER,
            severity=severity,
            explanation=explanation,
            evidence={
                "overall_severity": severity,
                "signal_count": len(assessment.signals),
                "signal_summary": signal_summary,
                "observation_kinds": kinds,
                "stale": stale,
                "provider": assessment.provider_meta.provider,
                "cache_status": assessment.provider_meta.cache_status,
            },
        )

    def _environment_driver(
        self,
        *,
        zone_id: str | None,
        hours: int,
    ) -> ResilienceDriverOut | None:
        try:
            assessment = self._environment.assess(zone_id=zone_id, hours=hours)
        except EnvironmentUnavailableError:
            return None

        stale = False
        if assessment.provider_meta is not None:
            stale = bool(assessment.provider_meta.stale)
        if assessment.note and "stale" in assessment.note.lower():
            stale = True

        signal_summary = []
        aqi_standard = None
        for s in assessment.signals[:8]:
            st = s.signal_type.value if hasattr(s.signal_type, "value") else str(s.signal_type)
            kind = (
                s.observation_kind.value
                if hasattr(s.observation_kind, "value")
                else str(s.observation_kind)
            )
            entry: dict[str, Any] = {
                "signal_type": st,
                "severity": s.severity,
                "observation_kind": kind,
            }
            std = s.metadata.get("aqi_standard") if isinstance(s.metadata, dict) else None
            if std:
                entry["aqi_standard"] = std
                aqi_standard = std
            signal_summary.append(entry)

        severity = self._normalize_severity(assessment.overall_severity)
        aqi_clause = ""
        if aqi_standard == "us_aqi":
            aqi_clause = " AQI references use US AQI (us_aqi), not CPCB/Indian AQI."
        explanation = (
            f"Environment impact overall severity {severity}"
            + ("; input stale" if stale else "")
            + "."
            + aqi_clause
        )
        return ResilienceDriverOut(
            source=ResilienceDriverSource.ENVIRONMENT,
            severity=severity,
            explanation=explanation,
            evidence={
                "overall_severity": severity,
                "signal_count": len(assessment.signals),
                "signal_summary": signal_summary,
                "stale": stale,
                "aqi_standard": aqi_standard,
                "provider": (
                    assessment.provider_meta.provider if assessment.provider_meta else None
                ),
            },
        )

    def _scenario_driver(self, *, zone_id: str | None) -> ResilienceDriverOut:
        state = self._scenarios.get_or_create()
        key, intensity = resolve_effective_scenario(
            global_key=state.scenario_key,
            global_intensity=float(state.intensity),
            zone_scenarios=getattr(state, "zone_scenarios", None) or {},
            zone_id=zone_id,
        )
        if key == ScenarioKey.NORMAL:
            return ResilienceDriverOut(
                source=ResilienceDriverSource.SCENARIO,
                severity="none",
                explanation="Scenario stress none (normal_operations).",
                evidence={
                    "scenario_key": key.value,
                    "intensity": intensity,
                    "default_severity": "none",
                },
            )

        try:
            definition = get_scenario(key.value)
            base = self._normalize_severity(definition.default_severity)
        except KeyError:
            base = "medium"

        severity = base
        elevated = False
        if intensity >= _SCENARIO_INTENSITY_ELEVATION and self._rank(base) < self._rank("critical"):
            severity = self._elevate_one(base)
            elevated = severity != base

        explanation = (
            f"Scenario '{key.value}' stress {severity} "
            f"(catalog default {base}, intensity {intensity:.2f}"
            + ("; elevated one step for intensity≥0.85" if elevated else "")
            + ")."
        )
        return ResilienceDriverOut(
            source=ResilienceDriverSource.SCENARIO,
            severity=severity,
            explanation=explanation,
            evidence={
                "scenario_key": key.value,
                "intensity": intensity,
                "default_severity": base,
                "intensity_elevated": elevated,
            },
        )

    def _asset_posture_driver(
        self,
        *,
        zone_id: str | None,
        utility_kind: str,
    ) -> tuple[ResilienceDriverOut, dict[str, int]]:
        assets = self._assets.list(utility_kind=utility_kind, zone_id=zone_id)
        high_crit = 0
        high_crit_no_backup = 0
        recovery_priority_counts: dict[str, int] = {}
        weather_exposure_counts: dict[str, int] = {}
        flood_risk_counts: dict[str, int] = {}

        for asset in assets:
            crit = (asset.criticality or "").strip().lower()
            backup = (asset.backup or "").strip().lower()
            if crit in {"high", "very-high", "very_high"}:
                high_crit += 1
                if backup == "none":
                    high_crit_no_backup += 1
            rp = str(getattr(asset, "recovery_priority", "") or "")
            recovery_priority_counts[rp] = recovery_priority_counts.get(rp, 0) + 1
            we = (asset.weather_exposure or "unknown").strip().lower()
            weather_exposure_counts[we] = weather_exposure_counts.get(we, 0) + 1
            fr = (asset.flood_risk or "unknown").strip().lower()
            flood_risk_counts[fr] = flood_risk_counts.get(fr, 0) + 1

        # Asset posture severity is informational evidence, not live hazard.
        if high_crit_no_backup >= _NO_BACKUP_CRITICAL_ELEVATION_COUNT:
            severity = "medium"
        elif high_crit_no_backup >= 1:
            severity = "low"
        else:
            severity = "none"

        stats = {
            "asset_count": len(assets),
            "high_criticality": high_crit,
            "high_criticality_no_backup": high_crit_no_backup,
        }
        explanation = (
            f"Static asset posture: {stats['asset_count']} assets; "
            f"{high_crit} high/very-high criticality; "
            f"{high_crit_no_backup} high/very-high criticality with backup=none. "
            "weather_exposure/flood_risk counts are seed metadata, not live measurements."
        )
        driver = ResilienceDriverOut(
            source=ResilienceDriverSource.ASSET_POSTURE,
            severity=severity,
            explanation=explanation,
            evidence={
                **stats,
                "recovery_priority_counts": recovery_priority_counts,
                "weather_exposure_counts": weather_exposure_counts,
                "flood_risk_counts": flood_risk_counts,
                "static_metadata_only": True,
            },
        )
        return driver, stats

    # Live operational triggers for dependency exposure (Phase 16.2 / 16.5).
    # Static asset.criticality is intentionally NOT used here.
    _DEPENDENCY_TRIGGERS = frozenset({"critical", "unavailable"})

    def _dependency_exposure_driver(
        self,
        *,
        zone_id: str | None,
    ) -> ResilienceDriverOut | None:
        """
        Aggregate CrossUtilityImpactService results for live critical/unavailable
        sources with outgoing supplies edges.

        Severity is always ``none`` — evidence only; not fed into overall posture.
        Cross-utility: scans all utilities in scope (not filtered by utility_kind).
        """
        from app.domain.enums import DependencyType
        from app.domain.repositories import AssetDependencyRepository
        from app.services.cross_utility_impact_service import CrossUtilityImpactService

        edges = AssetDependencyRepository(self._db).list(
            dependency_type=DependencyType.SUPPLIES.value,
            enabled=True,
        )
        source_ids = sorted({e.source_asset_id for e in edges})
        if not source_ids:
            return None

        impact_svc = CrossUtilityImpactService(self._db)
        exposures: list[dict[str, Any]] = []
        affected_utilities: set[str] = set()
        affected_zones: set[str] = set()
        affected_asset_ids: set[str] = set()
        explanation_lines: list[str] = []

        for source_id in source_ids:
            asset = self._assets.get(source_id)
            if asset is None:
                continue
            if zone_id and asset.zone_id != zone_id:
                continue
            trigger = self._resolve_live_dependency_trigger(asset)
            if trigger is None:
                continue

            assessment = impact_svc.assess_asset_impact(
                asset_id=source_id,
                condition=trigger,
            )
            if int(assessment.get("affected_asset_count") or 0) <= 0:
                continue

            affected_rows = list(assessment.get("affected_assets") or [])
            utils = [str(u) for u in (assessment.get("affected_utilities") or [])]
            zones = [str(z) for z in (assessment.get("affected_zones") or [])]
            paths = list(assessment.get("dependency_paths") or [])
            affected_utilities.update(utils)
            affected_zones.update(zones)
            for row in affected_rows:
                aid = str(row.get("asset_id") or "")
                if aid:
                    affected_asset_ids.add(aid)

            source_util = str(assessment.get("source_utility") or asset.utility_kind)
            source_zone = str(assessment.get("source_zone") or asset.zone_id)
            util_label = source_util.capitalize() if source_util else "Utility"
            dependents_txt = ", ".join(
                f"{str(r.get('utility') or '').capitalize()} "
                f"{r.get('asset_id')} ({r.get('zone')})"
                for r in affected_rows
            )
            explanation_lines.append(
                f"Critical {util_label} asset {source_id} ({trigger}) has modeled "
                f"downstream dependency exposure on {dependents_txt}. "
                "Potential/hypothetical exposure only — not a declared multi-utility outage."
            )
            exposures.append(
                {
                    "source_asset_id": source_id,
                    "source_utility": source_util,
                    "source_zone": source_zone,
                    "condition": trigger,
                    "affected_assets": [
                        {
                            "asset_id": str(r.get("asset_id") or ""),
                            "asset_name": str(r.get("asset_name") or ""),
                            "utility": str(r.get("utility") or ""),
                            "zone": str(r.get("zone") or ""),
                            "impact_kind": str(r.get("impact_kind") or ""),
                            "depth": int(r.get("depth") or 0),
                            "posture": str(r.get("posture") or "potentially_affected"),
                        }
                        for r in affected_rows
                    ],
                    "affected_utilities": utils,
                    "affected_zones": zones,
                    "dependency_paths": [
                        {
                            "asset_id": str(p.get("asset_id") or ""),
                            "depth": int(p.get("depth") or 0),
                            "impact_kind": str(p.get("impact_kind") or ""),
                            "path_asset_ids": list(p.get("path_asset_ids") or []),
                            "path_dependency_ids": list(p.get("path_dependency_ids") or []),
                        }
                        for p in paths
                    ],
                    "hypothetical": True,
                    "assessment_kind": str(
                        assessment.get("assessment_kind") or "explicit_dependency_impact"
                    ),
                }
            )

        if not exposures:
            return None

        explanation = " ".join(explanation_lines)
        return ResilienceDriverOut(
            source=ResilienceDriverSource.DEPENDENCY_EXPOSURE,
            # Evidence only — never included in overall posture max/elevation.
            severity="none",
            explanation=explanation,
            evidence={
                "exposure_count": len(exposures),
                "source_asset_ids": [e["source_asset_id"] for e in exposures],
                "affected_utilities": sorted(affected_utilities),
                "affected_zones": sorted(affected_zones),
                "affected_asset_ids": sorted(affected_asset_ids),
                "exposures": exposures,
                "hypothetical": True,
                "contributes_to_overall_posture": False,
                "note": (
                    "Modeled dependency exposure from explicit supplies edges via "
                    "CrossUtilityImpactService. Does not elevate resilience posture."
                ),
            },
        )

    @classmethod
    def _resolve_live_dependency_trigger(cls, asset: Any) -> str | None:
        """Same live trigger semantics as Phase 16.2 cross-utility recommendations."""
        profile = dict(asset.profile or {}) if isinstance(asset.profile, dict) else {}
        raw = str(profile.get("condition") or "").strip().lower()
        raw = raw.replace("-", "_").replace(" ", "_")
        if raw in {"offline", "failed"}:
            raw = "unavailable"
        if raw in cls._DEPENDENCY_TRIGGERS:
            return raw
        status = str(getattr(asset, "status", "") or "").strip().lower()
        if status in {"offline", "unavailable", "failed", "out_of_service"}:
            return "unavailable"
        return None

    @staticmethod
    def _normalize_severity(value: str | None) -> str:
        if not value:
            return "none"
        v = value.strip().lower()
        if v == "info":
            return "low"
        if v in _SEVERITY_RANK:
            return "none" if v == "info" else v
        return "none"

    @staticmethod
    def _rank(severity: str) -> int:
        return _SEVERITY_RANK.get(severity, 0)

    @classmethod
    def _max_severity(cls, *severities: str) -> str:
        best = "none"
        for s in severities:
            if cls._rank(s) > cls._rank(best):
                best = cls._normalize_severity(s)
        return best

    @classmethod
    def _elevate_one(cls, severity: str) -> str:
        rank = cls._rank(severity)
        if rank >= _SEVERITY_RANK["critical"]:
            return "critical"
        nxt = rank + 1
        # skip info
        if nxt == _SEVERITY_RANK["info"]:
            nxt = _SEVERITY_RANK["low"]
        return _RANK_TO_SEVERITY.get(nxt, "critical")
