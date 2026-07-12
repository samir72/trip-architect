from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.itinerary import Itinerary
from app.models.plan import Plan, ProposedRepair, RepairOutcome, SwapOutcome
from app.services.trip_service import TripService, get_trip_service
from app.store.plan_store import InvalidPlanTransitionError, PlanNotFoundError

router = APIRouter(prefix="/plans", tags=["plans"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="plan not found")


def _invalid(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/{plan_id}", response_model=Plan)
def get_plan(plan_id: str, service: TripService = Depends(get_trip_service)) -> Plan:
    try:
        return service.get_plan(plan_id)
    except PlanNotFoundError:
        raise _not_found() from None


@router.post("/{plan_id}/itineraries/{itinerary_id}/approve", response_model=Plan)
def approve(plan_id: str, itinerary_id: str, service: TripService = Depends(get_trip_service)) -> Plan:
    try:
        return service.approve(plan_id, itinerary_id)
    except PlanNotFoundError:
        raise _not_found() from None
    except InvalidPlanTransitionError as exc:
        raise _invalid(exc) from None


class SwapRequest(BaseModel):
    component_type: Literal["flight", "hotel", "activity"]
    component_id: str
    feedback: str


@router.post("/{plan_id}/itineraries/{itinerary_id}/swap", response_model=SwapOutcome)
async def swap(
    plan_id: str, itinerary_id: str, body: SwapRequest, service: TripService = Depends(get_trip_service)
) -> SwapOutcome:
    try:
        return await service.swap(plan_id, itinerary_id, body.component_type, body.component_id, body.feedback)
    except PlanNotFoundError:
        raise _not_found() from None
    except (InvalidPlanTransitionError, ValueError) as exc:
        raise _invalid(exc) from None


class RejectRequest(BaseModel):
    feedback: str


@router.post("/{plan_id}/reject", response_model=Plan)
async def reject(plan_id: str, body: RejectRequest, service: TripService = Depends(get_trip_service)) -> Plan:
    try:
        return await service.reject(plan_id, body.feedback)
    except PlanNotFoundError:
        raise _not_found() from None
    except InvalidPlanTransitionError as exc:
        raise _invalid(exc) from None


@router.post("/{plan_id}/undo", response_model=Plan)
def undo(plan_id: str, service: TripService = Depends(get_trip_service)) -> Plan:
    try:
        return service.undo(plan_id)
    except PlanNotFoundError:
        raise _not_found() from None
    except InvalidPlanTransitionError as exc:
        raise _invalid(exc) from None


class BookRequest(BaseModel):
    itinerary_id: str


class BookResponse(BaseModel):
    booking_id: str
    itinerary: Itinerary


@router.post("/{plan_id}/book", response_model=BookResponse)
def book(plan_id: str, body: BookRequest, service: TripService = Depends(get_trip_service)) -> BookResponse:
    try:
        plan = service.book(plan_id, body.itinerary_id)
    except PlanNotFoundError:
        raise _not_found() from None
    except InvalidPlanTransitionError as exc:
        raise _invalid(exc) from None
    itinerary = plan.itineraries[body.itinerary_id]
    return BookResponse(booking_id=f"bk-{plan.id}-{itinerary.id}", itinerary=itinerary)


@router.post("/{plan_id}/check-for-updates", response_model=list[ProposedRepair])
async def check_for_updates(plan_id: str, service: TripService = Depends(get_trip_service)) -> list[ProposedRepair]:
    try:
        return await service.check_for_updates(plan_id)
    except PlanNotFoundError:
        raise _not_found() from None


@router.post("/{plan_id}/repairs/{repair_id}/approve", response_model=RepairOutcome)
async def approve_repair(
    plan_id: str, repair_id: str, service: TripService = Depends(get_trip_service)
) -> RepairOutcome:
    try:
        return await service.approve_repair(plan_id, repair_id)
    except PlanNotFoundError:
        raise _not_found() from None
    except (InvalidPlanTransitionError, ValueError) as exc:
        raise _invalid(exc) from None


@router.post("/{plan_id}/repairs/{repair_id}/dismiss", response_model=Plan)
def dismiss_repair(plan_id: str, repair_id: str, service: TripService = Depends(get_trip_service)) -> Plan:
    try:
        return service.dismiss_repair(plan_id, repair_id)
    except PlanNotFoundError:
        raise _not_found() from None
    except InvalidPlanTransitionError as exc:
        raise _invalid(exc) from None
