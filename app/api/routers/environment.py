"""GET /environment - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/environment", response_model=MessageResponse)
def get_environment() -> MessageResponse:
    return MessageResponse(message="Environment endpoint scaffold - not implemented in Sprint 1")
