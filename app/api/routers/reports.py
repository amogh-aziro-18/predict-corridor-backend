"""GET /reports - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/reports", response_model=MessageResponse)
def list_reports() -> MessageResponse:
    return MessageResponse(message="Reports endpoint scaffold - not implemented in Sprint 1")
