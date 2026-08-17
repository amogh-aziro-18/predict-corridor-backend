"""GET /alerts - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/alerts", response_model=MessageResponse)
def list_alerts() -> MessageResponse:
    return MessageResponse(message="Alerts endpoint scaffold - not implemented in Sprint 1")
