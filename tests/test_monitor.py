from datetime import date

from app.models.constraints import Constraints, PartyComposition
from app.models.itinerary import ActivityOption, DayPlan, FlightOption, HotelOption, Itinerary
from app.supply.monitor import detect_price_drops, detect_unavailable_components


def _constraints() -> Constraints:
    return Constraints(
        origin="JFK", destination="Lisbon", start_date="2026-09-05", end_date="2026-09-12",
        party=PartyComposition(adults=2),
    )


def _flight(price_snapshot_usd: float | None = None, id_: str = "lis-nonstop-tap-jfk") -> FlightOption:
    return FlightOption(
        id=id_, price_usd=1, origin="JFK", destination="Lisbon",
        departure_date="2026-09-05", return_date="2026-09-12",
        airline="placeholder", nonstop=True, duration_minutes=1,
        price_snapshot_usd=price_snapshot_usd,
    )


def _hotel(
    price_snapshot_usd: float | None = None, id_: str = "lis-h-baixa-boutique", cancellation_deadline: str | None = None
) -> HotelOption:
    return HotelOption(
        id=id_, price_usd=1, name="placeholder", destination="Lisbon",
        check_in="2026-09-05", check_out="2026-09-12", nightly_rate_usd=1,
        price_snapshot_usd=price_snapshot_usd, cancellation_deadline=cancellation_deadline,
    )


def _activity(id_: str, price_snapshot_usd: float | None = None, date_: str = "2026-09-06") -> ActivityOption:
    return ActivityOption(
        id=id_, price_usd=1, name="placeholder", destination="Lisbon",
        date=date_, category="food", price_snapshot_usd=price_snapshot_usd,
    )


def _itinerary(flight: FlightOption, hotel: HotelOption, days: list[DayPlan] | None = None) -> Itinerary:
    return Itinerary(
        id="i1", title="t", summary="s", flight=flight, hotel=hotel,
        days=days or [], total_cost_usd=0,
    )


def test_detect_price_drops_finds_hotel_drop_within_deadline():
    # Real fixture price: lis-h-baixa-boutique is $145/night * 7 nights = $1015.
    hotel = _hotel(price_snapshot_usd=1200, cancellation_deadline="2027-01-01")
    itinerary = _itinerary(_flight(), hotel)
    drops = detect_price_drops(itinerary, _constraints())
    assert any(d.component_id == "lis-h-baixa-boutique" and d.new_price_usd == 1015 for d in drops)


def test_detect_price_drops_ignores_price_increase():
    hotel = _hotel(price_snapshot_usd=900)  # below the real $1015 -- not a drop
    itinerary = _itinerary(_flight(), hotel)
    drops = detect_price_drops(itinerary, _constraints())
    assert not any(d.component_id == "lis-h-baixa-boutique" for d in drops)


def test_detect_price_drops_excludes_past_hotel_deadline():
    hotel = _hotel(price_snapshot_usd=1200, cancellation_deadline="2020-01-01")
    itinerary = _itinerary(_flight(), hotel)
    drops = detect_price_drops(itinerary, _constraints(), today=date(2026, 7, 12))
    assert not any(d.component_id == "lis-h-baixa-boutique" for d in drops)


def test_detect_price_drops_treats_none_deadline_as_eligible():
    # Flights always have cancellation_deadline=None in this mock -- must
    # not be treated as "already expired."
    flight = _flight(price_snapshot_usd=1500)  # real price: $640 * 2 adults = $1280
    itinerary = _itinerary(flight, _hotel())
    drops = detect_price_drops(itinerary, _constraints())
    assert any(d.component_id == "lis-nonstop-tap-jfk" and d.new_price_usd == 1280 for d in drops)


def test_detect_unavailable_components_flags_missing_hotel():
    hotel = _hotel(id_="not-a-real-hotel")
    itinerary = _itinerary(_flight(), hotel)
    unavailable = detect_unavailable_components(itinerary, _constraints())
    assert any(d.component_type == "hotel" and d.component_id == "not-a-real-hotel" for d in unavailable)


def test_detect_unavailable_components_ignores_real_ids():
    itinerary = _itinerary(_flight(), _hotel())
    unavailable = detect_unavailable_components(itinerary, _constraints())
    assert unavailable == []


def test_detect_unavailable_components_dedups_repeated_activity_id():
    missing = "not-a-real-activity"
    days = [
        DayPlan(date="2026-09-06", activities=[_activity(missing, date_="2026-09-06")]),
        DayPlan(date="2026-09-07", activities=[_activity(missing, date_="2026-09-07")]),
    ]
    itinerary = _itinerary(_flight(), _hotel(), days=days)
    unavailable = detect_unavailable_components(itinerary, _constraints())
    matches = [d for d in unavailable if d.component_id == missing]
    assert len(matches) == 1
