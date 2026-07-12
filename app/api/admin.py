"""Demo/admin-only endpoints for triggering supply changes inside the live
server process. This can't be a standalone script: app/supply/provider.py's
fixture data is module-level state in this one uvicorn process (see
Dockerfile's --workers 1 and app/store/plan_store.py's docstring for why
this app is single-process/in-memory) -- a separate script process would
mutate its own, invisible copy of that memory.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.trip_service import TripService, get_trip_service
from app.store.plan_store import PlanNotFoundError
from app.supply import provider

router = APIRouter(prefix="/admin", tags=["admin"])


class SimulatePriceDropRequest(BaseModel):
    component_type: Literal["flight", "hotel", "activity"]
    component_id: str
    new_price_usd: float


@router.post("/plans/{plan_id}/simulate-price-drop")
def simulate_price_drop(
    plan_id: str, body: SimulatePriceDropRequest, service: TripService = Depends(get_trip_service)
) -> dict[str, str]:
    try:
        service.simulate_price_change(plan_id, body.component_type, body.component_id, body.new_price_usd)
    except PlanNotFoundError:
        raise HTTPException(status_code=404, detail="plan not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"status": "ok"}


class SimulateUnavailableRequest(BaseModel):
    component_type: Literal["flight", "hotel", "activity"]
    component_id: str


@router.post("/plans/{plan_id}/simulate-unavailable")
def simulate_unavailable(
    plan_id: str, body: SimulateUnavailableRequest, service: TripService = Depends(get_trip_service)
) -> dict[str, str]:
    try:
        service.simulate_unavailable(plan_id, body.component_type, body.component_id)
    except PlanNotFoundError:
        raise HTTPException(status_code=404, detail="plan not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"status": "ok"}


@router.post("/reset")
def reset() -> dict[str, str]:
    """Discards all simulated price changes/unavailability, restoring the
    original fixture data. Does not touch plans/sessions."""
    provider.simulate_reset()
    return {"status": "ok"}
