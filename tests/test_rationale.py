from app.models.constraints import Constraints
from app.models.itinerary import ActivityOption, DayPlan, FlightOption, HotelOption, Itinerary
from app.supply.rationale import (
    apply_rationale,
    rationale_for_activity,
    rationale_for_flight,
    rationale_for_hotel,
)


def test_rationale_for_flight_nonstop():
    flight = FlightOption(
        id="f1", price_usd=600, origin="JFK", destination="LIS",
        departure_date="2026-09-01", return_date="2026-09-07",
        airline="TAP", nonstop=True, duration_minutes=420,
    )
    assert "nonstop" in rationale_for_flight(flight).lower()


def test_rationale_for_flight_connecting():
    flight = FlightOption(
        id="f2", price_usd=470, origin="JFK", destination="LIS",
        departure_date="2026-09-01", return_date="2026-09-07",
        airline="United", nonstop=False, duration_minutes=690,
    )
    text = rationale_for_flight(flight)
    assert "United" in text
    assert "11h" in text


def test_rationale_for_hotel_cites_matched_tags_and_budget_and_cancellation():
    hotel = HotelOption(
        id="h1", price_usd=1015, name="Baixa Boutique Stay", destination="Lisbon",
        check_in="2026-09-01", check_out="2026-09-08", nightly_rate_usd=145,
        tags=["boutique", "walkable"], cancellation_deadline="2026-08-29",
    )
    constraints = Constraints(budget_usd=1500, vibe_tags=["boutique", "walkable"])
    text = rationale_for_hotel(hotel, constraints)
    assert "boutique" in text and "walkable" in text
    assert "under budget" in text
    assert "2026-08-29" in text


def test_rationale_for_hotel_falls_back_when_no_matches():
    hotel = HotelOption(
        id="h2", price_usd=630, name="Plain Hotel", destination="Lisbon",
        check_in="2026-09-01", check_out="2026-09-08", nightly_rate_usd=90, tags=[],
    )
    constraints = Constraints()
    assert rationale_for_hotel(hotel, constraints) == "Best available match for your stated preferences and dates."


def test_rationale_for_activity_matched_vs_fallback():
    activity = ActivityOption(
        id="a1", price_usd=85, name="Food Tour", destination="Lisbon",
        date="2026-09-02", category="food", tags=["food-forward"],
    )
    matched = rationale_for_activity(activity, Constraints(vibe_tags=["food-forward"]))
    assert "food-forward" in matched

    unmatched = rationale_for_activity(activity, Constraints(vibe_tags=["outdoors"]))
    assert "Popular food pick" in unmatched


def test_apply_rationale_fills_every_component():
    flight = FlightOption(
        id="f1", price_usd=600, origin="JFK", destination="LIS",
        departure_date="2026-09-01", return_date="2026-09-07",
        airline="TAP", nonstop=True, duration_minutes=420,
    )
    hotel = HotelOption(
        id="h1", price_usd=1015, name="Baixa Boutique Stay", destination="Lisbon",
        check_in="2026-09-01", check_out="2026-09-08", nightly_rate_usd=145, tags=["boutique"],
    )
    activity = ActivityOption(
        id="a1", price_usd=85, name="Food Tour", destination="Lisbon",
        date="2026-09-02", category="food", tags=["food-forward"],
    )
    itinerary = Itinerary(
        id="i1", title="test trip", summary="...", flight=flight, hotel=hotel,
        days=[DayPlan(date="2026-09-02", activities=[activity])], total_cost_usd=1700,
    )
    apply_rationale(itinerary, Constraints(vibe_tags=["boutique", "food-forward"]))
    assert itinerary.flight.rationale != ""
    assert itinerary.hotel.rationale != ""
    assert itinerary.days[0].activities[0].rationale != ""
