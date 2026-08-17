"""GET /technicians - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/technicians", response_model=MessageResponse)
def list_technicians() -> MessageResponse:
    return MessageResponse(message="Technicians endpoint scaffold - not implemented in Sprint 1")
