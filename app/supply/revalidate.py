"""Deterministic cross-component consistency checks, run after every swap.

Never trust the swap agent's own claim that "the rest of the itinerary still
works" -- compute it. Returns warnings (not hard failures): the traveler is
always the one who decides whether to proceed, per "preview / approve /
undo," so this surfaces concerns rather than blocking the swap outright.
"""

from __future__ import annotations

from app.models.constraints import Constraints
from app.models.itinerary import Itinerary


def _check_budget(itinerary: Itinerary, constraints: Constraints) -> list[str]:
    if constraints.budget_usd is not None and itinerary.total_cost_usd > constraints.budget_usd:
        over = itinerary.total_cost_usd - constraints.budget_usd
        return [
            f"Total cost ${itinerary.total_cost_usd:,.0f} exceeds your budget of "
            f"${constraints.budget_usd:,.0f} by ${over:,.0f}."
        ]
    return []


def _check_dates(itinerary: Itinerary, constraints: Constraints) -> list[str]:
    if not (constraints.start_date and constraints.end_date):
        return []

    warnings: list[str] = []
    if itinerary.flight.departure_date != constraints.start_date or itinerary.flight.return_date != constraints.end_date:
        warnings.append("Flight dates no longer match your requested travel window.")
    if itinerary.hotel.check_in != constraints.start_date or itinerary.hotel.check_out != constraints.end_date:
        warnings.append("Hotel dates no longer match your requested travel window.")
    for day in itinerary.days:
        for activity in day.activities:
            if not (constraints.start_date <= activity.date <= constraints.end_date):
                warnings.append(
                    f"'{activity.name}' is scheduled on {activity.date.isoformat()}, outside your travel window."
                )
    return warnings


def _check_party(itinerary: Itinerary, constraints: Constraints) -> list[str]:
    if constraints.party and constraints.party.children > 0 and "family-friendly" not in itinerary.hotel.tags:
        return ["Traveling with children, but the hotel isn't tagged family-friendly -- please verify it suits your group."]
    return []


def _check_non_negotiables(itinerary: Itinerary, constraints: Constraints) -> list[str]:
    haystack = " ".join(
        [itinerary.title.lower(), itinerary.summary.lower()]
        + [tag.lower() for tag in itinerary.hotel.tags]
        + [tag.lower() for day in itinerary.days for activity in day.activities for tag in activity.tags]
        + (["nonstop"] if itinerary.flight.nonstop else [])
    )
    warnings: list[str] = []
    for item in constraints.non_negotiables:
        keywords = [w.lower() for w in item.split() if len(w) > 3]
        if keywords and not any(keyword in haystack for keyword in keywords):
            warnings.append(f"Non-negotiable '{item}' may not be addressed by this itinerary -- please verify.")
    return warnings


def revalidate(itinerary: Itinerary, constraints: Constraints) -> list[str]:
    return [
        *_check_budget(itinerary, constraints),
        *_check_dates(itinerary, constraints),
        *_check_party(itinerary, constraints),
        *_check_non_negotiables(itinerary, constraints),
    ]
