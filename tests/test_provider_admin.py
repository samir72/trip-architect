import pytest

from app.models.itinerary import FlightOption
from app.supply.provider import (
    flight_id_prefix,
    search_activities,
    search_flights,
    search_hotels,
    simulate_activity_price_change,
    simulate_flight_price_change,
    simulate_hotel_price_change,
    simulate_reset,
    simulate_unavailable,
)


def test_simulate_hotel_price_change_reflected_in_next_search():
    simulate_hotel_price_change("Lisbon", "lis-h-baixa-boutique", new_nightly_rate_usd=99)
    hotels = search_hotels("Lisbon", "2026-09-05", "2026-09-12")
    hotel = next(h for h in hotels if h["id"] == "lis-h-baixa-boutique")
    assert hotel["price_usd"] == 99 * 7


def test_simulate_hotel_price_change_unknown_id_raises():
    with pytest.raises(ValueError):
        simulate_hotel_price_change("Lisbon", "not-a-real-hotel", new_nightly_rate_usd=99)


def test_simulate_activity_price_change_reflected_in_next_search():
    simulate_activity_price_change("Lisbon", "lis-a-food-tour", new_price_usd=1)
    activities = search_activities("Lisbon")
    activity = next(a for a in activities if a["id"] == "lis-a-food-tour")
    assert activity["price_usd"] == 1


def test_simulate_unavailable_excludes_hotel_from_search():
    simulate_unavailable("hotel", "lis-h-baixa-boutique")
    hotels = search_hotels("Lisbon", "2026-09-05", "2026-09-12")
    assert not any(h["id"] == "lis-h-baixa-boutique" for h in hotels)


def test_simulate_unavailable_excludes_activity_from_search():
    simulate_unavailable("activity", "lis-a-food-tour")
    activities = search_activities("Lisbon")
    assert not any(a["id"] == "lis-a-food-tour" for a in activities)


def test_simulate_reset_restores_original_state():
    simulate_hotel_price_change("Lisbon", "lis-h-baixa-boutique", new_nightly_rate_usd=1)
    simulate_unavailable("hotel", "lis-h-baixa-boutique")
    simulate_reset()

    hotels = search_hotels("Lisbon", "2026-09-05", "2026-09-12")
    hotel = next(h for h in hotels if h["id"] == "lis-h-baixa-boutique")
    assert hotel["price_usd"] == 145 * 7  # original fixture price, restored


def test_flight_id_prefix_round_trip_with_simulate_flight_price_change():
    flights = search_flights("JFK", "Lisbon", "2026-09-05", "2026-09-12", adults=1)
    original = next(f for f in flights if f["airline"] == "TAP Air Portugal")
    flight = FlightOption(
        id=original["id"], price_usd=original["price_usd"], origin="JFK", destination="Lisbon",
        departure_date="2026-09-05", return_date="2026-09-12", airline=original["airline"],
        nonstop=original["nonstop"], duration_minutes=original["duration_minutes"],
    )

    prefix = flight_id_prefix(flight)
    simulate_flight_price_change("Lisbon", prefix, new_price_usd=1)

    refreshed = search_flights("JFK", "Lisbon", "2026-09-05", "2026-09-12", adults=1)
    updated = next(f for f in refreshed if f["id"] == flight.id)
    assert updated["price_usd"] == 1
