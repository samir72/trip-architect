from app.models.constraints import Constraints, PartyComposition
from app.models.itinerary import ActivityOption, DayPlan, FlightOption, HotelOption, Itinerary
from app.supply.revalidate import revalidate


def _itinerary(**overrides) -> Itinerary:
    flight = overrides.get("flight") or FlightOption(
        id="f1", price_usd=600, origin="JFK", destination="Lisbon",
        departure_date="2026-09-05", return_date="2026-09-12",
        airline="TAP", nonstop=True, duration_minutes=420,
    )
    hotel = overrides.get("hotel") or HotelOption(
        id="h1", price_usd=1000, name="Baixa Boutique Stay", destination="Lisbon",
        check_in="2026-09-05", check_out="2026-09-12", nightly_rate_usd=145, tags=["boutique", "walkable"],
    )
    return Itinerary(
        id="i1", title="the boutique food trip", summary="A walkable, food-forward week, avoiding touristy spots.",
        flight=flight, hotel=hotel, days=overrides.get("days", []),
        total_cost_usd=overrides.get("total_cost_usd", 1600),
    )


def test_no_warnings_for_a_fully_consistent_itinerary():
    constraints = Constraints(
        start_date="2026-09-05", end_date="2026-09-12", budget_usd=2000,
        non_negotiables=["avoid touristy areas"],
    )
    assert revalidate(_itinerary(), constraints) == []


def test_warns_when_over_budget():
    constraints = Constraints(budget_usd=1000)
    warnings = revalidate(_itinerary(total_cost_usd=1600), constraints)
    assert any("exceeds your budget" in w for w in warnings)


def test_warns_when_flight_dates_drift_from_constraints():
    constraints = Constraints(start_date="2026-09-01", end_date="2026-09-08")
    warnings = revalidate(_itinerary(), constraints)  # itinerary flight is 09-05..09-12
    assert any("Flight dates" in w for w in warnings)


def test_warns_when_activity_falls_outside_travel_window():
    activity = ActivityOption(
        id="a1", price_usd=50, name="Late Add-on", destination="Lisbon",
        date="2026-09-20", category="food",
    )
    constraints = Constraints(start_date="2026-09-05", end_date="2026-09-12")
    warnings = revalidate(_itinerary(days=[DayPlan(date="2026-09-20", activities=[activity])]), constraints)
    assert any("outside your travel window" in w for w in warnings)


def test_warns_when_traveling_with_children_and_hotel_not_family_friendly():
    constraints = Constraints(party=PartyComposition(adults=2, children=1, child_ages=[5]))
    warnings = revalidate(_itinerary(), constraints)
    assert any("family-friendly" in w for w in warnings)


def test_no_party_warning_when_hotel_is_family_friendly():
    hotel = HotelOption(
        id="h2", price_usd=1200, name="Family Lodge", destination="Lisbon",
        check_in="2026-09-05", check_out="2026-09-12", nightly_rate_usd=170, tags=["family-friendly"],
    )
    constraints = Constraints(party=PartyComposition(adults=2, children=1, child_ages=[5]))
    warnings = revalidate(_itinerary(hotel=hotel, total_cost_usd=1800), constraints)
    assert not any("family-friendly" in w for w in warnings)


def test_warns_when_non_negotiable_not_reflected_anywhere():
    constraints = Constraints(non_negotiables=["ocean view balcony"])
    warnings = revalidate(_itinerary(), constraints)
    assert any("ocean view balcony" in w for w in warnings)


def test_no_warning_when_non_negotiable_keyword_present_in_summary():
    constraints = Constraints(non_negotiables=["avoid touristy areas"])
    warnings = revalidate(_itinerary(), constraints)  # summary mentions "touristy"
    assert not any("avoid touristy areas" in w for w in warnings)
