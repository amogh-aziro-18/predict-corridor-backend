"""GET /weather - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/weather", response_model=MessageResponse)
def list_weather() -> MessageResponse:
    return MessageResponse(message="Weather endpoint scaffold - not implemented in Sprint 1")
