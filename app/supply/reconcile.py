"""Ground agent-selected components in the actual mocked supply record for
their id, and recompute pricing from those grounded facts.

Shared by the composition and swap agents: both hand back components picked
from live tool-call results, but only the id is trusted from that output --
every other field (price, cancellation deadline, tags, name) is overwritten
from a fresh, authoritative supply lookup before rationale/pricing is
computed, so nothing downstream is based on something the LLM transcribed
(or invented) rather than actually returned by search.
"""

from __future__ import annotations

from app.models.constraints import Constraints
from app.models.itinerary import ComponentBase, Itinerary
from app.supply.provider import search_activities, search_flights, search_hotels
from app.supply.rationale import apply_rationale


def merge_authoritative(component: ComponentBase, authoritative: dict) -> ComponentBase:
    """Overwrite a component's facts with an authoritative supply record,
    re-validating so date strings coerce properly, while preserving any
    fields the record doesn't carry (rationale, and for activities, which
    day it's scheduled)."""
    merged = component.model_dump(mode="python")
    merged.update(authoritative)
    return type(component).model_validate(merged)


def lookup_authoritative(
    itinerary: Itinerary, constraints: Constraints
) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    """Fresh authoritative supply records for this itinerary's resolved
    destination/dates, keyed by id: (flights_by_id, hotels_by_id, activities_by_id).
    Shared by reconcile_with_supply and app/supply/monitor.py's detection
    functions, which need the exact same "what's really available right
    now" lookup reconcile already performs."""
    resolved_destination = itinerary.hotel.destination or itinerary.flight.destination
    adults = constraints.party.adults if constraints.party else 1

    departure_date = (constraints.start_date or itinerary.flight.departure_date).isoformat()
    return_date = (constraints.end_date or itinerary.flight.return_date).isoformat()
    check_in = (constraints.start_date or itinerary.hotel.check_in).isoformat()
    check_out = (constraints.end_date or itinerary.hotel.check_out).isoformat()

    flights_by_id = {
        f["id"]: f
        for f in search_flights(
            constraints.origin or itinerary.flight.origin, resolved_destination, departure_date, return_date, adults=adults
        )
    }
    hotels_by_id = {
        h["id"]: h
        for h in search_hotels(resolved_destination, check_in, check_out, vibe_tags=constraints.vibe_tags)
    }
    activities_by_id = {
        a["id"]: a for a in search_activities(resolved_destination, vibe_tags=constraints.vibe_tags)
    }
    return flights_by_id, hotels_by_id, activities_by_id


def reconcile_with_supply(itinerary: Itinerary, constraints: Constraints) -> None:
    flights_by_id, hotels_by_id, activities_by_id = lookup_authoritative(itinerary, constraints)

    if itinerary.flight.id in flights_by_id:
        itinerary.flight = merge_authoritative(itinerary.flight, flights_by_id[itinerary.flight.id])
    if itinerary.hotel.id in hotels_by_id:
        itinerary.hotel = merge_authoritative(itinerary.hotel, hotels_by_id[itinerary.hotel.id])
    for day in itinerary.days:
        day.activities = [
            merge_authoritative(activity, activities_by_id[activity.id])
            if activity.id in activities_by_id
            else activity
            for activity in day.activities
        ]


def reconcile_and_price(itinerary: Itinerary, constraints: Constraints) -> Itinerary:
    reconcile_with_supply(itinerary, constraints)
    apply_rationale(itinerary, constraints)
    itinerary.total_cost_usd = round(sum(c.price_usd for c in itinerary.all_components()), 2)
    return itinerary
