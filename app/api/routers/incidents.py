"""GET /incidents - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/incidents", response_model=MessageResponse)
def list_incidents() -> MessageResponse:
    return MessageResponse(message="Incidents endpoint scaffold - not implemented in Sprint 1")
