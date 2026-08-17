"""GET /kpis - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/kpis", response_model=MessageResponse)
def list_kpis() -> MessageResponse:
    return MessageResponse(message="KPIs endpoint scaffold - not implemented in Sprint 1")
