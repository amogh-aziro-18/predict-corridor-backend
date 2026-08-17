"""GET /digital-twin - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/digital-twin", response_model=MessageResponse)
def get_digital_twin() -> MessageResponse:
    return MessageResponse(message="Digital twin endpoint scaffold - not implemented in Sprint 1")
