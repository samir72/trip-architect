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
from typing import Annotated, Any

from pydantic import Field

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, list[dict[str, Any]]]:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


_FLIGHTS = _load("flights.json")
_HOTELS = _load("hotels.json")
_ACTIVITIES = _load("activities.json")


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
    ]
    if vibe_tags:
        results.sort(key=lambda r: len(set(r["tags"]) & set(vibe_tags)), reverse=True)
    return results
