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
    """Two candidates only count as "the same trip" if they share every
    component id -- hotel, flight, and every activity. A shared hotel
    forced by a non-negotiable (e.g. only one family-friendly hotel in a
    city) is legitimate as long as the flight and/or activities still
    differ; only a candidate that's identical end-to-end is a real dupe."""
    signatures = [frozenset(c.id for c in itin.all_components()) for itin in itineraries]
    passed = len(set(signatures)) == len(signatures)
    detail = f"component signatures: {[sorted(sig) for sig in signatures]}"
    return CheckResult("distinct_candidates", passed, detail)


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
    """Positional comparison, not id-set comparison -- duplicate activity
    ids within one itinerary (near-guaranteed once a trip has more days
    than the destination has unique activities) make set arithmetic
    unreliable in both directions: it can flag a correct swap as wrong (the
    targeted id recurs elsewhere, so removing it from the set removes ALL
    occurrences, not just the one slot) and miss a real violation (two
    slots change, but the id sets still happen to balance)."""

    def slots(itin: Itinerary) -> list[tuple[str, str]]:
        result = [("flight", itin.flight.id), ("hotel", itin.hotel.id)]
        for day_index, day in enumerate(itin.days):
            for activity_index, activity in enumerate(day.activities):
                result.append((f"day{day_index}.activity{activity_index}", activity.id))
        return result

    old_slots, new_slots = slots(old_itinerary), slots(new_itinerary)
    if len(old_slots) != len(new_slots):
        return CheckResult(
            "swap_only_target_changed", False, f"slot count changed: {len(old_slots)} -> {len(new_slots)}"
        )

    changed = [(label, o, n) for (label, o), (_, n) in zip(old_slots, new_slots) if o != n]
    passed = len(changed) == 1 and changed[0][1] == old_component_id and changed[0][2] == new_component_id
    detail = f"changed slots: {changed}" if changed else "no slots changed"
    return CheckResult("swap_only_target_changed", passed, detail)
