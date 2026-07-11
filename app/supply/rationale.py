"""Deterministic "why" text generation.

The composition/swap agents select components; this module explains the
selection from the component's own fields. The LLM never authors rationale
text itself -- this is the single biggest lever against a demo audience
catching a hallucinated claim (e.g. citing a cancellation date that isn't
the record's actual date).
"""

from app.models.constraints import Constraints
from app.models.itinerary import ActivityOption, FlightOption, HotelOption, Itinerary


def _matched_tags(component_tags: list[str], vibe_tags: list[str]) -> list[str]:
    return [t for t in component_tags if t in vibe_tags]


def rationale_for_flight(flight: FlightOption) -> str:
    if flight.nonstop:
        return "Only nonstop option within your date window."
    hours = flight.duration_minutes // 60
    return f"Connecting flight via {flight.airline}, {hours}h total -- cheaper than the nonstop option."


def rationale_for_hotel(hotel: HotelOption, constraints: Constraints) -> str:
    parts: list[str] = []
    matched = _matched_tags(hotel.tags, constraints.vibe_tags)
    if matched:
        parts.append(f"matches '{', '.join(matched)}' preference")

    if constraints.budget_usd:
        nights = max((hotel.check_out - hotel.check_in).days, 1)
        total = hotel.nightly_rate_usd * nights
        pct_under = (constraints.budget_usd - total) / constraints.budget_usd * 100
        if pct_under > 0:
            parts.append(f"{pct_under:.0f}% under budget")

    if hotel.cancellation_deadline:
        parts.append(f"free cancellation to {hotel.cancellation_deadline.isoformat()}")

    return "; ".join(parts) if parts else "Best available match for your stated preferences and dates."


def rationale_for_activity(activity: ActivityOption, constraints: Constraints) -> str:
    matched = _matched_tags(activity.tags, constraints.vibe_tags)
    if matched:
        return f"Matches your interest in {', '.join(matched)}."
    return f"Popular {activity.category} pick in {activity.destination}."


def apply_rationale(itinerary: Itinerary, constraints: Constraints) -> Itinerary:
    """Stamp deterministic rationale onto every component of an itinerary, in place."""
    itinerary.flight.rationale = rationale_for_flight(itinerary.flight)
    itinerary.hotel.rationale = rationale_for_hotel(itinerary.hotel, constraints)
    for day in itinerary.days:
        for activity in day.activities:
            activity.rationale = rationale_for_activity(activity, constraints)
    return itinerary
