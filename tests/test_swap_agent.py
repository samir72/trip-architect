import pytest

from app.agents.swap_agent import _locate_new_component_id, diff_components
from app.models.itinerary import ActivityOption, DayPlan, FlightOption, HotelOption, Itinerary


def _base_itinerary(activities: list[ActivityOption]) -> Itinerary:
    flight = FlightOption(
        id="f1", price_usd=600, origin="JFK", destination="Lisbon",
        departure_date="2026-09-05", return_date="2026-09-12",
        airline="TAP", nonstop=True, duration_minutes=420,
    )
    hotel = HotelOption(
        id="h1", price_usd=1000, name="Baixa Boutique Stay", destination="Lisbon",
        check_in="2026-09-05", check_out="2026-09-12", nightly_rate_usd=145,
    )
    return Itinerary(
        id="i1", title="t", summary="s", flight=flight, hotel=hotel,
        days=[DayPlan(date="2026-09-06", activities=activities)], total_cost_usd=1600,
    )


def _activity(id_: str, name: str = "Food Tour") -> ActivityOption:
    return ActivityOption(id=id_, price_usd=85, name=name, destination="Lisbon", date="2026-09-06", category="food")


def test_diff_components_reports_only_changed_fields():
    old = _activity("a1", name="Food Tour")
    new = _activity("a2", name="Wine Tasting")
    diff = diff_components(old, new)
    fields = {d.field for d in diff}
    assert "id" in fields and "name" in fields
    assert "destination" not in fields  # unchanged


def test_diff_components_ignores_rationale():
    old = _activity("a1")
    old.rationale = "old reason"
    new = _activity("a1")
    new.rationale = "new reason"
    assert diff_components(old, new) == []


def test_locate_new_component_id_for_flight_slot():
    old_itin = _base_itinerary([_activity("a1")])
    new_itin = _base_itinerary([_activity("a1")])
    new_itin.flight = new_itin.flight.model_copy(update={"id": "f2", "airline": "United"})
    assert _locate_new_component_id(old_itin, new_itin, "flight") == "f2"


def test_locate_new_component_id_for_activity_slot_finds_unseen_id():
    old_itin = _base_itinerary([_activity("a1")])
    new_itin = _base_itinerary([_activity("a2", name="Wine Tasting")])
    assert _locate_new_component_id(old_itin, new_itin, "activity") == "a2"


def test_locate_new_component_id_raises_when_nothing_changed():
    old_itin = _base_itinerary([_activity("a1")])
    new_itin = _base_itinerary([_activity("a1")])  # agent echoed the same activity back
    with pytest.raises(ValueError):
        _locate_new_component_id(old_itin, new_itin, "activity")
