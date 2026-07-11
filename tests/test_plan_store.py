import pytest

from app.models.constraints import Constraints
from app.models.itinerary import ActivityOption, DayPlan, FlightOption, HotelOption, Itinerary, ItineraryStatus
from app.models.plan import Plan, PlanStatus
from app.store.plan_store import InvalidPlanTransitionError, PlanNotFoundError, PlanStore


def _flight(id_: str) -> FlightOption:
    return FlightOption(
        id=id_, price_usd=600, origin="JFK", destination="LIS",
        departure_date="2026-09-01", return_date="2026-09-07",
        airline="TAP", nonstop=True, duration_minutes=420,
    )


def _hotel(id_: str, name: str = "Hotel A") -> HotelOption:
    return HotelOption(
        id=id_, price_usd=1000, name=name, destination="Lisbon",
        check_in="2026-09-01", check_out="2026-09-08", nightly_rate_usd=145,
    )


def _itinerary(id_: str, status: ItineraryStatus = ItineraryStatus.PROPOSED) -> Itinerary:
    return Itinerary(
        id=id_, title=f"trip {id_}", summary="...",
        flight=_flight(f"f-{id_}"), hotel=_hotel(f"h-{id_}"),
        days=[], total_cost_usd=1600, status=status,
    )


def _new_plan(store: PlanStore) -> Plan:
    plan = Plan(constraints=Constraints())
    return store.create(plan)


def test_record_composition_sets_candidates_and_status():
    store = PlanStore()
    plan = _new_plan(store)
    store.record_composition(plan.id, [_itinerary("a"), _itinerary("b")])
    plan = store.get(plan.id)
    assert plan.status == PlanStatus.REVIEWING
    assert plan.candidate_order == ["a", "b"]
    assert set(plan.itineraries) == {"a", "b"}


def test_swap_replaces_component_and_bumps_version():
    store = PlanStore()
    plan = _new_plan(store)
    store.record_composition(plan.id, [_itinerary("a")])
    updated = _itinerary("a")
    updated.hotel = _hotel("h-a-new", name="Hotel B")
    store.swap_component(plan.id, "a", updated)
    plan = store.get(plan.id)
    assert plan.itineraries["a"].hotel.name == "Hotel B"
    assert plan.itineraries["a"].version == 2


def test_swap_on_approved_itinerary_demotes_to_proposed():
    store = PlanStore()
    plan = _new_plan(store)
    store.record_composition(plan.id, [_itinerary("a")])
    store.approve(plan.id, "a")
    assert store.get(plan.id).itineraries["a"].status == ItineraryStatus.APPROVED

    updated = _itinerary("a", status=ItineraryStatus.APPROVED)
    store.swap_component(plan.id, "a", updated)
    assert store.get(plan.id).itineraries["a"].status == ItineraryStatus.PROPOSED


def test_undo_after_swap_restores_previous_component():
    store = PlanStore()
    plan = _new_plan(store)
    store.record_composition(plan.id, [_itinerary("a")])
    updated = _itinerary("a")
    updated.hotel = _hotel("h-a-new", name="Hotel B")
    store.swap_component(plan.id, "a", updated)

    store.undo(plan.id)
    plan = store.get(plan.id)
    assert plan.itineraries["a"].hotel.name == "Hotel A"


def test_undo_after_approve_restores_proposed_status():
    store = PlanStore()
    plan = _new_plan(store)
    store.record_composition(plan.id, [_itinerary("a")])
    store.approve(plan.id, "a")
    assert store.get(plan.id).itineraries["a"].status == ItineraryStatus.APPROVED

    store.undo(plan.id)
    assert store.get(plan.id).itineraries["a"].status == ItineraryStatus.PROPOSED


def test_undo_after_recompose_restores_old_candidate_set():
    store = PlanStore()
    plan = _new_plan(store)
    store.record_composition(plan.id, [_itinerary("a"), _itinerary("b")])
    store.reject_recompose(plan.id, [_itinerary("c"), _itinerary("d")], feedback="too pricey")
    assert set(store.get(plan.id).itineraries) == {"c", "d"}

    store.undo(plan.id)
    plan = store.get(plan.id)
    assert set(plan.itineraries) == {"a", "b"}
    assert plan.candidate_order == ["a", "b"]


def test_undo_of_initial_composition_resets_to_composing():
    store = PlanStore()
    plan = _new_plan(store)
    store.record_composition(plan.id, [_itinerary("a")])

    store.undo(plan.id)
    plan = store.get(plan.id)
    assert plan.itineraries == {}
    assert plan.status == PlanStatus.COMPOSING


def test_book_requires_approval_first():
    store = PlanStore()
    plan = _new_plan(store)
    store.record_composition(plan.id, [_itinerary("a")])
    with pytest.raises(InvalidPlanTransitionError):
        store.book(plan.id, "a")


def test_book_locks_plan_against_further_changes():
    store = PlanStore()
    plan = _new_plan(store)
    store.record_composition(plan.id, [_itinerary("a")])
    store.approve(plan.id, "a")
    store.book(plan.id, "a")

    plan = store.get(plan.id)
    assert plan.status == PlanStatus.BOOKED
    assert plan.itineraries["a"].status == ItineraryStatus.BOOKED
    assert plan.itineraries["a"].price_snapshot_usd == 1600

    with pytest.raises(InvalidPlanTransitionError):
        store.approve(plan.id, "a")
    with pytest.raises(InvalidPlanTransitionError):
        store.swap_component(plan.id, "a", _itinerary("a"))
    with pytest.raises(InvalidPlanTransitionError):
        store.reject_recompose(plan.id, [_itinerary("z")], feedback="x")


def test_undo_after_book_reverses_booking():
    store = PlanStore()
    plan = _new_plan(store)
    store.record_composition(plan.id, [_itinerary("a")])
    store.approve(plan.id, "a")
    store.book(plan.id, "a")

    store.undo(plan.id)
    plan = store.get(plan.id)
    assert plan.status == PlanStatus.REVIEWING
    assert plan.booked_itinerary_id is None
    assert plan.itineraries["a"].status == ItineraryStatus.APPROVED


def test_undo_with_no_events_raises():
    store = PlanStore()
    plan = _new_plan(store)
    with pytest.raises(InvalidPlanTransitionError):
        store.undo(plan.id)


def test_get_missing_plan_raises():
    store = PlanStore()
    with pytest.raises(PlanNotFoundError):
        store.get("does-not-exist")
