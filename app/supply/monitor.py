"""Deterministic post-booking change detection: price drops and components
that have disappeared from supply entirely. No agent knowledge here --
matches the revalidate.py/rationale.py pattern. Detecting a disruption is
deterministic; only *finding a replacement* for one needs an LLM, which is
why that's a separate step in app/services/trip_service.py, not this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.models.constraints import Constraints
from app.models.itinerary import ComponentBase, Itinerary
from app.supply.reconcile import lookup_authoritative

ComponentType = Literal["flight", "hotel", "activity"]


@dataclass
class PriceDropDetection:
    component_type: ComponentType
    component_id: str
    old_price_usd: float
    new_price_usd: float


@dataclass
class UnavailableDetection:
    component_type: ComponentType
    component_id: str


def _unique_components(itinerary: Itinerary) -> list[tuple[ComponentType, ComponentBase]]:
    """(component_type, component) pairs, with activities deduped by id --
    a small fixture catalog means the same activity legitimately appears on
    multiple days, and each should only be checked/reported once."""
    seen_activity_ids: set[str] = set()
    pairs: list[tuple[ComponentType, ComponentBase]] = [
        ("flight", itinerary.flight),
        ("hotel", itinerary.hotel),
    ]
    for day in itinerary.days:
        for activity in day.activities:
            if activity.id in seen_activity_ids:
                continue
            seen_activity_ids.add(activity.id)
            pairs.append(("activity", activity))
    return pairs


def _is_eligible(component: ComponentBase, today: date) -> bool:
    """Only hotels carry a real cancellation_deadline in this mock (flights
    and activities always have None) -- None must mean "no restriction,"
    matching rationale_for_hotel's existing convention, or price-drop
    detection would silently never fire for 2 of 3 component types."""
    return component.cancellation_deadline is None or component.cancellation_deadline >= today


def detect_price_drops(
    itinerary: Itinerary, constraints: Constraints, today: date | None = None
) -> list[PriceDropDetection]:
    today = today or date.today()
    flights_by_id, hotels_by_id, activities_by_id = lookup_authoritative(itinerary, constraints)
    authoritative_by_type: dict[ComponentType, dict[str, dict]] = {
        "flight": flights_by_id,
        "hotel": hotels_by_id,
        "activity": activities_by_id,
    }

    drops: list[PriceDropDetection] = []
    for component_type, component in _unique_components(itinerary):
        if component.price_snapshot_usd is None or not _is_eligible(component, today):
            continue
        authoritative = authoritative_by_type[component_type].get(component.id)
        if authoritative is None:
            continue  # unavailable, not a price drop -- see detect_unavailable_components
        new_price = authoritative["price_usd"]
        if new_price < component.price_snapshot_usd:
            drops.append(
                PriceDropDetection(
                    component_type=component_type,
                    component_id=component.id,
                    old_price_usd=component.price_snapshot_usd,
                    new_price_usd=new_price,
                )
            )
    return drops


def detect_unavailable_components(itinerary: Itinerary, constraints: Constraints) -> list[UnavailableDetection]:
    flights_by_id, hotels_by_id, activities_by_id = lookup_authoritative(itinerary, constraints)
    authoritative_by_type: dict[ComponentType, dict[str, dict]] = {
        "flight": flights_by_id,
        "hotel": hotels_by_id,
        "activity": activities_by_id,
    }

    unavailable: list[UnavailableDetection] = []
    for component_type, component in _unique_components(itinerary):
        if component.id not in authoritative_by_type[component_type]:
            unavailable.append(UnavailableDetection(component_type=component_type, component_id=component.id))
    return unavailable
