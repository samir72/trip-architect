import pytest

from app.agents.swap_agent import _locate_new_component_id, diff_components
from app.models.itinerary import ActivityOption, DayPlan, FlightOption, HotelOption, Itinerary


def _flight() -> FlightOption:
    return FlightOption(
        id="f1", price_usd=600, origin="JFK", destination="Lisbon",
        departure_date="2026-09-05", return_date="2026-09-12",
        airline="TAP", nonstop=True, duration_minutes=420,
    )


def _hotel() -> HotelOption:
    return HotelOption(
        id="h1", price_usd=1000, name="Baixa Boutique Stay", destination="Lisbon",
        check_in="2026-09-05", check_out="2026-09-12", nightly_rate_usd=145,
    )


def _base_itinerary(activities: list[ActivityOption]) -> Itinerary:
    return Itinerary(
        id="i1", title="t", summary="s", flight=_flight(), hotel=_hotel(),
        days=[DayPlan(date="2026-09-06", activities=activities)], total_cost_usd=1600,
    )


def _multi_day_itinerary(days: list[list[ActivityOption]]) -> Itinerary:
    return Itinerary(
        id="i1", title="t", summary="s", flight=_flight(), hotel=_hotel(),
        days=[DayPlan(date=f"2026-09-{6 + i:02d}", activities=acts) for i, acts in enumerate(days)],
        total_cost_usd=1600,
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
    new_id, position = _locate_new_component_id(old_itin, new_itin, "flight", "f1")
    assert new_id == "f2"
    assert position is None


def test_locate_new_component_id_for_activity_slot_finds_unseen_id():
    old_itin = _base_itinerary([_activity("a1")])
    new_itin = _base_itinerary([_activity("a2", name="Wine Tasting")])
    new_id, position = _locate_new_component_id(old_itin, new_itin, "activity", "a1")
    assert new_id == "a2"
    assert position == (0, 0)


def test_locate_new_component_id_raises_when_nothing_changed():
    old_itin = _base_itinerary([_activity("a1")])
    new_itin = _base_itinerary([_activity("a1")])  # agent echoed the same activity back
    with pytest.raises(ValueError):
        _locate_new_component_id(old_itin, new_itin, "activity", "a1")


def test_locate_new_component_id_handles_duplicate_activity_ids_across_days():
    """Matches the live repro that caused the original bug: an activity
    present on two days. Swapping the first occurrence to something that
    already exists on another day must still be detected as a valid,
    distinguishable swap -- a plain "id not seen anywhere in the old
    itinerary" check would miss this."""
    old_itin = _multi_day_itinerary(
        [[_activity("tram", name="Tram Tour")], [_activity("food", name="Food Tour")]]
    )
    new_itin = _multi_day_itinerary(
        [[_activity("food", name="Food Tour")], [_activity("food", name="Food Tour")]]
    )
    new_id, position = _locate_new_component_id(old_itin, new_itin, "activity", "tram")
    assert new_id == "food"
    assert position == (0, 0)


def test_locate_new_component_id_raises_when_hotel_echoed_back_unchanged():
    """A hotel swap that returns the same hotel (e.g. no strictly better
    option existed) must raise, not be reported as a successful no-op
    swap -- matches the live case where Lisbon's only boutique-tagged
    hotel was the one already in place."""
    old_itin = _base_itinerary([_activity("a1")])
    new_itin = _base_itinerary([_activity("a1")])  # hotel untouched
    with pytest.raises(ValueError):
        _locate_new_component_id(old_itin, new_itin, "hotel", "h1")


def test_locate_new_component_id_raises_when_duplicate_slot_echoed_unchanged():
    """Same duplicate-id setup as above, but the targeted slot is echoed
    back unchanged -- must still raise, confirming the no-op guard survives
    even when the target's id happens to recur elsewhere in the trip."""
    old_itin = _multi_day_itinerary(
        [[_activity("tram", name="Tram Tour")], [_activity("food", name="Food Tour")]]
    )
    new_itin = _multi_day_itinerary(
        [[_activity("tram", name="Tram Tour")], [_activity("food", name="Food Tour")]]
    )
    with pytest.raises(ValueError):
        _locate_new_component_id(old_itin, new_itin, "activity", "tram")
