"""GET /gis - placeholder."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/gis", response_model=MessageResponse)
def get_gis() -> MessageResponse:
    return MessageResponse(message="GIS endpoint scaffold - not implemented in Sprint 1")
