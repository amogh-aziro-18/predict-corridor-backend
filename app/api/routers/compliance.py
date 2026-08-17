"""GET /compliance - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/compliance", response_model=MessageResponse)
def list_compliance() -> MessageResponse:
    return MessageResponse(message="Compliance endpoint scaffold - not implemented in Sprint 1")
