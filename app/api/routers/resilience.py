"""GET /resilience - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/resilience", response_model=MessageResponse)
def list_resilience() -> MessageResponse:
    return MessageResponse(message="Resilience endpoint scaffold - not implemented in Sprint 1")
