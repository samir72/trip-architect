"""Deterministic check functions, reused across composition and swap
scenarios. Each returns a CheckResult -- never raises, so a battery run
never aborts partway through on an assertion.

These deliberately reuse the same production logic the app itself trusts
(revalidate(), the supply search functions) rather than re-implementing
scoring rules -- an eval that disagreed with the app's own notion of
"valid" would be testing the wrong thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.constraints import Constraints
from app.models.itinerary import ComponentBase, Itinerary
from app.supply.provider import search_activities, search_flights, search_hotels
from app.supply.revalidate import revalidate
from evals.scenarios import SUPPORTED_CITIES


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def check_destination_resolved(itinerary: Itinerary, expected_city: str | None) -> CheckResult:
    resolved = itinerary.hotel.destination
    if expected_city is not None:
        passed = resolved == expected_city
        detail = f"resolved to {resolved!r}, expected {expected_city!r}"
    else:
        passed = resolved in SUPPORTED_CITIES
        detail = f"resolved to {resolved!r}, expected one of {SUPPORTED_CITIES}"
    return CheckResult("destination_resolved", passed, detail)


def check_distinct_candidates(itineraries: list[Itinerary]) -> CheckResult:
    hotel_ids = [itin.hotel.id for itin in itineraries]
    passed = len(set(hotel_ids)) == len(hotel_ids)
    return CheckResult("distinct_candidates", passed, f"hotel ids: {hotel_ids}")


def check_grounded_in_supply(itinerary: Itinerary, constraints: Constraints) -> CheckResult:
    """Independently re-derive the same authoritative lookup reconcile.py
    uses, and confirm every component's id is a real, current supply id."""
    destination = itinerary.hotel.destination or itinerary.flight.destination
    adults = constraints.party.adults if constraints.party else 1
    departure_date = (constraints.start_date or itinerary.flight.departure_date).isoformat()
    return_date = (constraints.end_date or itinerary.flight.return_date).isoformat()
    check_in = (constraints.start_date or itinerary.hotel.check_in).isoformat()
    check_out = (constraints.end_date or itinerary.hotel.check_out).isoformat()

    flight_ids = {
        f["id"]
        for f in search_flights(
            constraints.origin or itinerary.flight.origin, destination, departure_date, return_date, adults=adults
        )
    }
    hotel_ids = {h["id"] for h in search_hotels(destination, check_in, check_out)}
    activity_ids = {a["id"] for a in search_activities(destination)}

    missing = []
    if itinerary.flight.id not in flight_ids:
        missing.append(f"flight:{itinerary.flight.id}")
    if itinerary.hotel.id not in hotel_ids:
        missing.append(f"hotel:{itinerary.hotel.id}")
    for day in itinerary.days:
        for activity in day.activities:
            if activity.id not in activity_ids:
                missing.append(f"activity:{activity.id}")

    passed = not missing
    detail = "all components grounded in real supply" if passed else f"ungrounded: {missing}"
    return CheckResult("grounded_in_supply", passed, detail)


def check_revalidate(itinerary: Itinerary, constraints: Constraints, expect_warnings: bool) -> CheckResult:
    warnings = revalidate(itinerary, constraints)
    if expect_warnings:
        passed = len(warnings) > 0
        detail = f"expected warnings, got: {warnings or 'none'}"
    else:
        passed = len(warnings) == 0
        detail = f"expected no warnings, got: {warnings or 'none'}"
    return CheckResult("revalidate", passed, detail)


def check_swap_price_direction(old: ComponentBase, new: ComponentBase, direction: Literal["cheaper", "pricier"]) -> CheckResult:
    passed = new.price_usd < old.price_usd if direction == "cheaper" else new.price_usd > old.price_usd
    return CheckResult("swap_price_direction", passed, f"${old.price_usd} -> ${new.price_usd} (expected {direction})")


def check_swap_tag_if_achievable(
    new_component: ComponentBase,
    old_component: ComponentBase,
    expected_tag: str,
    component_type: Literal["hotel", "activity"],
    destination: str,
    constraints: Constraints,
) -> CheckResult:
    """Only fails if the tag was actually achievable -- i.e. some fixture
    *other than the one already in place* carries it. If the old component
    was already the unique holder of that tag, "swap to something more X"
    has no valid target, and that's not the agent's fault."""
    if component_type == "hotel":
        pool = search_hotels(destination, constraints.start_date.isoformat(), constraints.end_date.isoformat())
    else:
        pool = search_activities(destination)

    achievable = any(expected_tag in item.get("tags", []) and item["id"] != old_component.id for item in pool)
    if not achievable:
        return CheckResult(
            "swap_tag_match", True,
            f"no alternative {component_type} in {destination} other than the current one carries "
            f"{expected_tag!r} -- check skipped (unwinnable by construction)",
        )

    has_tag = expected_tag in getattr(new_component, "tags", [])
    return CheckResult("swap_tag_match", has_tag, f"expected {expected_tag!r} in {getattr(new_component, 'tags', [])}")


def check_swap_only_target_changed(
    old_itinerary: Itinerary, new_itinerary: Itinerary, old_component_id: str, new_component_id: str
) -> CheckResult:
    old_ids = {c.id for c in old_itinerary.all_components()}
    new_ids = {c.id for c in new_itinerary.all_components()}
    expected_new_ids = (old_ids - {old_component_id}) | {new_component_id}
    passed = new_ids == expected_new_ids
    detail = f"new ids: {sorted(new_ids)}; expected: {sorted(expected_new_ids)}"
    return CheckResult("swap_only_target_changed", passed, detail)
