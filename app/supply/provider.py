"""Deterministic mocked supply search functions.

These are exposed directly to the composition/swap agents as tool functions
(Agent Framework introspects the type hints + docstring to build the
function-calling schema), so signatures and return shapes are kept simple and
JSON-serializable -- plain dicts with ISO date strings, not Pydantic models or
date objects.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

if TYPE_CHECKING:
    from app.models.itinerary import FlightOption

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, list[dict[str, Any]]]:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


_FLIGHTS = _load("flights.json")
_HOTELS = _load("hotels.json")
_ACTIVITIES = _load("activities.json")

# Runtime-only overlay for the post-booking-monitoring demo: ids "removed"
# from supply by simulate_unavailable(). Cleared by simulate_reset(), never
# persisted -- consistent with the rest of this app having no persistence
# layer at all.
_unavailable_ids: set[str] = set()


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def search_flights(
    origin: Annotated[str, Field(description="Origin airport code or city, e.g. 'JFK'")],
    destination: Annotated[str, Field(description="Destination city, e.g. 'Lisbon'")],
    departure_date: Annotated[str, Field(description="Departure date, YYYY-MM-DD")],
    return_date: Annotated[str, Field(description="Return date, YYYY-MM-DD")],
    adults: Annotated[int, Field(description="Number of adult travelers")] = 1,
) -> list[dict[str, Any]]:
    """Search mocked round-trip flight inventory to a destination."""
    templates = _FLIGHTS.get(destination, [])
    return [
        {
            "id": f"{t['id_prefix']}-{origin.lower()}",
            "price_usd": round(t["price_usd"] * max(adults, 1), 2),
            "cancellation_deadline": None,
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "return_date": return_date,
            "airline": t["airline"],
            "nonstop": t["nonstop"],
            "duration_minutes": t["duration_minutes"],
        }
        for t in templates
        if f"{t['id_prefix']}-{origin.lower()}" not in _unavailable_ids
    ]


def search_hotels(
    destination: Annotated[str, Field(description="Destination city, e.g. 'Lisbon'")],
    check_in: Annotated[str, Field(description="Check-in date, YYYY-MM-DD")],
    check_out: Annotated[str, Field(description="Check-out date, YYYY-MM-DD")],
    vibe_tags: Annotated[
        list[str] | None, Field(description="Preference tags to prioritize, e.g. ['boutique', 'walkable']")
    ] = None,
) -> list[dict[str, Any]]:
    """Search mocked hotel inventory in a destination for a date range."""
    check_in_date = _parse_date(check_in)
    check_out_date = _parse_date(check_out)
    nights = max((check_out_date - check_in_date).days, 1)
    results = []
    for h in _HOTELS.get(destination, []):
        if h["id"] in _unavailable_ids:
            continue
        deadline = check_in_date - timedelta(days=h["cancellation_deadline_days_before_checkin"])
        results.append(
            {
                "id": h["id"],
                "price_usd": round(h["nightly_rate_usd"] * nights, 2),
                "cancellation_deadline": deadline.isoformat(),
                "name": h["name"],
                "destination": destination,
                "check_in": check_in,
                "check_out": check_out,
                "nightly_rate_usd": h["nightly_rate_usd"],
                "tags": h["tags"],
            }
        )
    if vibe_tags:
        results.sort(key=lambda r: len(set(r["tags"]) & set(vibe_tags)), reverse=True)
    return results


def search_activities(
    destination: Annotated[str, Field(description="Destination city, e.g. 'Lisbon'")],
    vibe_tags: Annotated[
        list[str] | None, Field(description="Preference tags to prioritize, e.g. ['food-forward', 'outdoors']")
    ] = None,
) -> list[dict[str, Any]]:
    """Search mocked activities/restaurants/experiences in a destination."""
    results = [
        {
            "id": a["id"],
            "price_usd": a["price_usd"],
            "cancellation_deadline": None,
            "name": a["name"],
            "destination": destination,
            "category": a["category"],
            "tags": a["tags"],
        }
        for a in _ACTIVITIES.get(destination, [])
        if a["id"] not in _unavailable_ids
    ]
    if vibe_tags:
        results.sort(key=lambda r: len(set(r["tags"]) & set(vibe_tags)), reverse=True)
    return results


def simulate_hotel_price_change(destination: str, hotel_id: str, new_nightly_rate_usd: float) -> None:
    """Demo/admin-only: mutate a hotel fixture's nightly rate in place, for
    the post-booking-monitoring feature to detect on its next check. Must
    be called inside the live server process -- see app/api/admin.py."""
    for h in _HOTELS.get(destination, []):
        if h["id"] == hotel_id:
            h["nightly_rate_usd"] = new_nightly_rate_usd
            return
    raise ValueError(f"unknown hotel {hotel_id!r} in {destination!r}")


def simulate_flight_price_change(destination: str, id_prefix: str, new_price_usd: float) -> None:
    """new_price_usd is the per-passenger base fare (matches the fixture's
    own unit -- search_flights multiplies this by `adults` at search time)."""
    for t in _FLIGHTS.get(destination, []):
        if t["id_prefix"] == id_prefix:
            t["price_usd"] = new_price_usd
            return
    raise ValueError(f"unknown flight id_prefix {id_prefix!r} in {destination!r}")


def simulate_activity_price_change(destination: str, activity_id: str, new_price_usd: float) -> None:
    for a in _ACTIVITIES.get(destination, []):
        if a["id"] == activity_id:
            a["price_usd"] = new_price_usd
            return
    raise ValueError(f"unknown activity {activity_id!r} in {destination!r}")


def simulate_unavailable(component_type: Literal["flight", "hotel", "activity"], key: str) -> None:
    """key is always the component's actual, final id (the same id a booked
    FlightOption/HotelOption/ActivityOption carries) -- for flights that's
    the constructed "{id_prefix}-{origin}" id, not the bare id_prefix."""
    _unavailable_ids.add(key)


def simulate_reset() -> None:
    """Clears the unavailable-ids overlay and reloads all three fixture
    files from disk, discarding any simulated price changes. Runtime-only,
    like everything else simulate_* touches -- nothing here is persisted."""
    global _FLIGHTS, _HOTELS, _ACTIVITIES
    _FLIGHTS = _load("flights.json")
    _HOTELS = _load("hotels.json")
    _ACTIVITIES = _load("activities.json")
    _unavailable_ids.clear()


def flight_id_prefix(flight: "FlightOption") -> str:
    """Inverse of search_flights' id construction (f"{id_prefix}-{origin.lower()}"),
    needed because a booked FlightOption only carries the constructed id,
    not the fixture's own id_prefix key that simulate_flight_price_change needs."""
    suffix = f"-{flight.origin.lower()}"
    assert flight.id.endswith(suffix), f"flight id {flight.id!r} doesn't end with {suffix!r}"
    return flight.id[: -len(suffix)]
