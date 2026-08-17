"""Agentic operational intelligence API (Sprint 7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agents.services.agent_service import AgentService
from app.api.deps import db_dep
from app.api.schemas.agent import OperationalSummaryOut

router = APIRouter()


@router.get("/agents/operational-summary", response_model=OperationalSummaryOut)
def operational_summary(db: Session = Depends(db_dep)) -> OperationalSummaryOut:
    """Execute the LangGraph workflow over existing services."""
    return OperationalSummaryOut(**AgentService(db).operational_summary())
