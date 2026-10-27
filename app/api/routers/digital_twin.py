"""GET /digital-twin — discovery pointer to operational simulation APIs."""

from fastapi import APIRouter

from app.api.schemas.common import MessageResponse

router = APIRouter()


@router.get("/digital-twin", response_model=MessageResponse)
def get_digital_twin() -> MessageResponse:
    return MessageResponse(
        message=(
            "Use POST /api/simulations/what-if for isolated operational scenario simulation. "
            "Baseline: GET /api/simulations/baseline/{asset_id}. "
            "Catalog: GET /api/simulations/scenarios. "
            "Does not mutate live telemetry."
        )
    )
