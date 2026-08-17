"""Operational recommendation service — telemetry + scenario driven."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.api.schemas.recommendation import RecommendationOut
from app.core.errors import ValidationAppError
from app.domain.repositories.scenario_repository import ScenarioRepository
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
        drafts = self._intel.generate(
            scenario_key=state.scenario_key,
            scenario_intensity=float(state.intensity),
        )
        # Predictions are optional until the explicit Sprint 6C train operation
        # creates an artifact. Existing recommendation APIs remain available.
        try:
            predictions = PredictiveMaintenanceService(self._db).predict_all()
        except ValidationAppError:
            predictions = []
        drafts.extend(self._intel.maintenance_prediction_rules(predictions))
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
        return [
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
                scenario_key=state.scenario_key,
                rule_code=d.rule_code,
            )
            for d in drafts
        ]
