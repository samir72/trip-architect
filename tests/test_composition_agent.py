from app.agents.composition_agent import _finalize, _merge_authoritative, _reconcile_with_supply
from app.models.constraints import Constraints, PartyComposition
from app.models.itinerary import ActivityOption, DayPlan, FlightOption, HotelOption, Itinerary, ItineraryStatus


def _fabricated_itinerary() -> Itinerary:
    """An itinerary shaped like something the LLM might return: real ids
    from the Lisbon fixtures, but with garbled/invented prices and dates to
    simulate transcription drift or hallucination."""
    flight = FlightOption(
        id="lis-nonstop-tap-jfk", price_usd=999, origin="JFK", destination="Lisbon",
        departure_date="2026-09-05", return_date="2026-09-12",
        airline="Wrong Airline", nonstop=False, duration_minutes=1,
    )
    hotel = HotelOption(
        id="lis-h-baixa-boutique", price_usd=1, name="Wrong Name", destination="Lisbon",
        check_in="2026-09-05", check_out="2026-09-12", nightly_rate_usd=1, tags=[],
    )
    activity = ActivityOption(
        id="lis-a-food-tour", price_usd=1, name="Wrong Name", destination="Lisbon",
        date="2026-09-06", category="wrong", tags=[],
    )
    return Itinerary(
        id="i1", title="the boutique food trip", summary="...",
        flight=flight, hotel=hotel, days=[DayPlan(date="2026-09-06", activities=[activity])],
        total_cost_usd=3,  # deliberately wrong, should be recomputed
    )


def _constraints() -> Constraints:
    return Constraints(
        origin="JFK", destination="Portugal", start_date="2026-09-05", end_date="2026-09-12",
        budget_usd=3000, party=PartyComposition(adults=2), vibe_tags=["boutique", "food-forward"],
    )


def test_merge_authoritative_coerces_types_and_preserves_untouched_fields():
    activity = ActivityOption(
        id="lis-a-food-tour", price_usd=1, name="Wrong Name", destination="Lisbon",
        date="2026-09-06", category="wrong", tags=[], rationale="stale",
    )
    authoritative = {
        "id": "lis-a-food-tour", "price_usd": 85, "cancellation_deadline": None,
        "name": "Alfama Food & Wine Tour", "destination": "Lisbon", "category": "food",
        "tags": ["food-forward", "walkable"],
    }
    merged = _merge_authoritative(activity, authoritative)
    assert merged.price_usd == 85
    assert merged.name == "Alfama Food & Wine Tour"
    assert merged.date.isoformat() == "2026-09-06"  # preserved, not in authoritative dict
    assert merged.rationale == "stale"  # preserved, overwritten later by apply_rationale


def test_reconcile_with_supply_overwrites_fabricated_prices_and_dates():
    itinerary = _fabricated_itinerary()
    _reconcile_with_supply(itinerary, _constraints())

    assert itinerary.flight.price_usd == 640 * 2  # nonstop TAP fixture price * 2 adults
    assert itinerary.flight.airline == "TAP Air Portugal"
    assert itinerary.flight.nonstop is True

    assert itinerary.hotel.price_usd == 145 * 7  # nightly rate * 7 nights
    assert itinerary.hotel.name == "Baixa Boutique Stay"
    assert itinerary.hotel.cancellation_deadline.isoformat() == "2026-09-02"  # 3 days before check-in

    activity = itinerary.days[0].activities[0]
    assert activity.price_usd == 85
    assert activity.name == "Alfama Food & Wine Tour"
    assert activity.date.isoformat() == "2026-09-06"  # untouched, not a supply fact


def test_reconcile_leaves_unknown_ids_untouched():
    itinerary = _fabricated_itinerary()
    itinerary.hotel.id = "not-a-real-hotel"
    _reconcile_with_supply(itinerary, _constraints())
    assert itinerary.hotel.name == "Wrong Name"  # left as-is, not silently dropped


def test_finalize_recomputes_total_and_sets_status():
    itinerary = _fabricated_itinerary()
    finalized = _finalize(itinerary, _constraints())

    expected_total = round(finalized.flight.price_usd + finalized.hotel.price_usd + finalized.days[0].activities[0].price_usd, 2)
    assert finalized.total_cost_usd == expected_total
    assert finalized.total_cost_usd != 3  # not the fabricated total
    assert finalized.status == ItineraryStatus.PROPOSED
    assert finalized.version == 1
    assert finalized.flight.rationale != ""
    assert finalized.hotel.rationale != ""
    assert finalized.days[0].activities[0].rationale != ""
