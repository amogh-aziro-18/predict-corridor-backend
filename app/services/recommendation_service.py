"""Operational recommendation service — telemetry, scenario, maintenance, weather, environment, resilience, cross-utility."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.api.schemas.recommendation import RecommendationOut
from app.core.errors import EnvironmentUnavailableError, ValidationAppError, WeatherUnavailableError
from app.domain.repositories.scenario_repository import ScenarioRepository
from app.scenarios.resolution import resolve_effective_scenario
from app.services.operational_intelligence_service import OperationalIntelligenceService
from app.services.predictive_maintenance_service import PredictiveMaintenanceService


class RecommendationService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._scenario_repo = ScenarioRepository(db)
        self._intel = OperationalIntelligenceService(db)

    def list_recommendations(self) -> list[RecommendationOut]:
        return self._to_out(self._drafts())

    def list_high_priority(self) -> list[RecommendationOut]:
        drafts = self._drafts()
        filtered = [d for d in drafts if d.priority in OperationalIntelligenceService.HIGH_PRIORITIES]
        return self._to_out(filtered)

    def _drafts(self):
        state = self._scenario_repo.get_or_create()
        zone_scenarios = getattr(state, "zone_scenarios", None) or {}
        drafts = self._intel.generate(
            scenario_key=state.scenario_key,
            scenario_intensity=float(state.intensity),
            zone_scenarios=zone_scenarios,
        )
        # Predictions are optional until the explicit Sprint 6C train operation
        # creates an artifact. Existing recommendation APIs remain available.
        try:
            predictions = PredictiveMaintenanceService(self._db).predict_all()
        except ValidationAppError:
            predictions = []
        drafts.extend(self._intel.maintenance_prediction_rules(predictions))
        try:
            from app.services.power_predictive_maintenance_service import (
                PowerPredictiveMaintenanceService,
            )

            power_predictions = PowerPredictiveMaintenanceService(self._db).predict_all()
        except ValidationAppError:
            power_predictions = []
        drafts.extend(self._intel.power_maintenance_prediction_rules(power_predictions))
        try:
            from app.services.gas_predictive_maintenance_service import (
                GasPredictiveMaintenanceService,
            )

            gas_predictions = GasPredictiveMaintenanceService(self._db).predict_all()
        except ValidationAppError:
            gas_predictions = []
        drafts.extend(self._intel.gas_maintenance_prediction_rules(gas_predictions))
        try:
            drafts.extend(self._intel.weather_impact_rules())
        except WeatherUnavailableError:
            pass
        try:
            drafts.extend(self._intel.environment_impact_rules())
        except EnvironmentUnavailableError:
            pass
        # Resilience composes weather/env internally; per-driver failures are handled
        # inside ResilienceAssessmentService (no whole-pipeline abort).
        drafts.extend(self._intel.resilience_rules())
        # Phase 16.2 — consume CrossUtilityImpactService (no second traversal path).
        drafts.extend(self._intel.cross_utility_dependency_rules())
        priority_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        return sorted(
            drafts,
            key=lambda draft: (
                -priority_rank.get(draft.priority, 0),
                -draft.confidence,
                draft.title,
            ),
        )

    def _to_out(self, drafts) -> list[RecommendationOut]:
        now = datetime.now(timezone.utc)
        state = self._scenario_repo.get_or_create()
        zone_scenarios = getattr(state, "zone_scenarios", None) or {}

        out: list[RecommendationOut] = []
        for d in drafts:
            zone_key, _intensity = resolve_effective_scenario(
                global_key=state.scenario_key,
                global_intensity=float(state.intensity),
                zone_scenarios=zone_scenarios,
                zone_id=d.affected_zone,
            )
            out.append(
                RecommendationOut(
                    id=OperationalIntelligenceService.stable_id(d.rule_code, d.affected_asset),
                    priority=d.priority,
                    severity=d.severity,
                    title=d.title,
                    description=d.description,
                    affected_asset=d.affected_asset,
                    affected_zone=d.affected_zone,
                    recommendation=d.recommendation,
                    estimated_business_impact=d.estimated_business_impact,
                    confidence=d.confidence,
                    generated_at=now,
                    scenario_key=zone_key.value,
                    rule_code=d.rule_code,
                    source=_infer_recommendation_source(d.rule_code),
                    context=getattr(d, "context", None),
                )
            )
        return out


def _infer_recommendation_source(rule_code: str | None) -> str | None:
    if not rule_code:
        return None
    if rule_code.startswith("cross_utility_"):
        return "cross_utility"
    if rule_code.startswith("power_xgboost_"):
        return "maintenance"
    if rule_code.startswith("gas_xgboost_"):
        return "maintenance"
    if rule_code.startswith("power_"):
        return "telemetry"
    if rule_code.startswith("gas_"):
        return "telemetry"
    if rule_code.startswith("communications_"):
        return "telemetry"
    if rule_code.startswith("resilience_"):
        return "resilience"
    if rule_code.startswith("environment_"):
        return "environment"
    if rule_code.startswith("weather_"):
        return "weather"
    if rule_code == "xgboost_maintenance_risk":
        return "maintenance"
    if rule_code.startswith("scenario_"):
        return "scenario"
    return "telemetry"
