"""GET /operations/work-orders - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/operations/work-orders", response_model=MessageResponse)
def list_work_orders() -> MessageResponse:
    return MessageResponse(message="Work orders endpoint scaffold - not implemented in Sprint 1")
