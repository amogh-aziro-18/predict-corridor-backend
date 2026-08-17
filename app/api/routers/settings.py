"""GET /settings - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/settings", response_model=MessageResponse)
def get_settings_route() -> MessageResponse:
    return MessageResponse(message="Settings endpoint scaffold - not implemented in Sprint 1")
