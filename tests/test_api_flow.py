"""End-to-end API tests with agents replaced by StubAgent -- no real Azure
OpenAI calls, so this runs in CI without credentials. Live agent behavior is
covered separately by the manual scripts in scripts/manual_test_*.py."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.agents.intent_agent import ExtractedConstraints, IntentTurnResult
from app.main import app
from app.models.itinerary import ActivityOption, DayPlan, FlightOption, HotelOption, Itinerary
from app.models.plan import ItineraryCandidates
from app.services.trip_service import TripService, get_trip_service
from app.store.plan_store import PlanStore
from app.store.session_store import SessionStore


class _StubResult:
    def __init__(self, value):
        self.value = value


class StubAgent:
    """Duck-types agent_framework.Agent.run(): app code only ever calls
    `await agent.run(prompt, options={"response_format": SomeModel})` and
    reads `.value`, so no real Agent Framework object is needed here."""

    def __init__(self, responder):
        self._responder = responder

    async def run(self, prompt, options=None):
        response_format = (options or {}).get("response_format")
        return _StubResult(self._responder(prompt, response_format))


def _lisbon_flight(id_: str = "lis-nonstop-tap-jfk") -> FlightOption:
    return FlightOption(
        id=id_, price_usd=0, origin="JFK", destination="Lisbon",
        departure_date="2026-09-05", return_date="2026-09-12",
        airline="placeholder", nonstop=True, duration_minutes=1,
    )


def _lisbon_hotel(id_: str) -> HotelOption:
    return HotelOption(
        id=id_, price_usd=0, name="placeholder", destination="Lisbon",
        check_in="2026-09-05", check_out="2026-09-12", nightly_rate_usd=0,
    )


def _lisbon_activity(id_: str = "lis-a-food-tour") -> ActivityOption:
    return ActivityOption(
        id=id_, price_usd=0, name="placeholder", destination="Lisbon",
        date="2026-09-06", category="food",
    )


def _canned_itinerary(itin_id: str, hotel_id: str = "lis-h-baixa-boutique") -> Itinerary:
    """A stub agent's "output": real fixture ids with placeholder (0) prices
    -- reconcile_and_price is what fills in the real numbers, and these
    tests assert on the reconciled values to prove that pipeline runs."""
    return Itinerary(
        id=itin_id, title=f"trip {itin_id}", summary="a Lisbon trip",
        flight=_lisbon_flight(), hotel=_lisbon_hotel(hotel_id),
        days=[DayPlan(date="2026-09-06", activities=[_lisbon_activity()])],
        total_cost_usd=0,
    )


def _intent_responder(prompt, response_format) -> IntentTurnResult:
    return IntentTurnResult(
        assistant_reply="Great, I have everything I need!",
        extracted=ExtractedConstraints(
            origin="JFK", destination="Lisbon",
            start_date=date(2026, 9, 5), end_date=date(2026, 9, 12),
            budget_usd=4000, adults=2, children=0,
            vibe_tags=["boutique", "walkable"],
        ),
    )


def _composition_responder(prompt, response_format) -> ItineraryCandidates:
    return ItineraryCandidates(
        itineraries=[_canned_itinerary("i1"), _canned_itinerary("i2", hotel_id="lis-h-alfama-view")]
    )


def _swap_responder(prompt, response_format) -> Itinerary:
    return _canned_itinerary("i1", hotel_id="lis-h-alfama-view")


def _make_service(**overrides) -> TripService:
    defaults = dict(
        session_store=SessionStore(),
        plan_store=PlanStore(),
        intent_agent=StubAgent(_intent_responder),
        composition_agent=StubAgent(_composition_responder),
        swap_agent=StubAgent(_swap_responder),
    )
    defaults.update(overrides)
    return TripService(**defaults)


@pytest.fixture
def client():
    service = _make_service()
    app.dependency_overrides[get_trip_service] = lambda: service
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_full_lifecycle_through_the_rest_api(client):
    session_id = client.post("/sessions").json()["session_id"]

    msg_resp = client.post(f"/sessions/{session_id}/messages", json={"message": "a week in Lisbon"}).json()
    assert msg_resp["constraints_complete"] is True

    plan = client.post(f"/sessions/{session_id}/compose").json()
    plan_id = plan["id"]
    assert len(plan["candidate_order"]) == 2
    itin_id = plan["candidate_order"][0]
    # Reconciled from real fixture prices, not the stub's placeholder 0.
    assert plan["itineraries"][itin_id]["total_cost_usd"] > 0

    plan = client.post(f"/plans/{plan_id}/itineraries/{itin_id}/approve").json()
    assert plan["itineraries"][itin_id]["status"] == "approved"

    swap_resp = client.post(
        f"/plans/{plan_id}/itineraries/{itin_id}/swap",
        json={
            "component_type": "hotel",
            "component_id": plan["itineraries"][itin_id]["hotel"]["id"],
            "feedback": "cheaper please",
        },
    ).json()
    assert swap_resp["itinerary"]["status"] == "proposed"  # demoted after swapping an approved itinerary
    assert any(d["field"] == "id" for d in swap_resp["diff"])

    client.post(f"/plans/{plan_id}/undo")
    plan = client.get(f"/plans/{plan_id}").json()
    assert plan["itineraries"][itin_id]["status"] == "approved"
    assert plan["itineraries"][itin_id]["hotel"]["id"] == "lis-h-baixa-boutique"  # swap reversed

    booking = client.post(f"/plans/{plan_id}/book", json={"itinerary_id": itin_id}).json()
    assert booking["itinerary"]["status"] == "booked"

    locked = client.post(f"/plans/{plan_id}/itineraries/{itin_id}/approve")
    assert locked.status_code == 409


def test_compose_before_constraints_complete_returns_400(client):
    session_id = client.post("/sessions").json()["session_id"]
    resp = client.post(f"/sessions/{session_id}/compose")
    assert resp.status_code == 400


def test_unknown_session_returns_404(client):
    assert client.get("/sessions/does-not-exist").status_code == 404


def test_unknown_plan_returns_404(client):
    assert client.get("/plans/does-not-exist").status_code == 404


def test_swap_with_unknown_component_returns_409(client):
    session_id = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{session_id}/messages", json={"message": "a week in Lisbon"})
    plan = client.post(f"/sessions/{session_id}/compose").json()
    itin_id = plan["candidate_order"][0]

    resp = client.post(
        f"/plans/{plan['id']}/itineraries/{itin_id}/swap",
        json={"component_type": "hotel", "component_id": "not-a-real-id", "feedback": "x"},
    )
    assert resp.status_code == 409


def test_single_candidate_plan_handled_correctly(client):
    """Edge case: composition returning the schema-minimum of 1 itinerary
    (not the usual 2-3) shouldn't break candidate_order/approve/book."""
    service = _make_service(
        composition_agent=StubAgent(lambda p, rf: ItineraryCandidates(itineraries=[_canned_itinerary("solo")]))
    )
    app.dependency_overrides[get_trip_service] = lambda: service

    session_id = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{session_id}/messages", json={"message": "a week in Lisbon"})
    plan = client.post(f"/sessions/{session_id}/compose").json()
    assert plan["candidate_order"] == ["solo"]

    approved = client.post(f"/plans/{plan['id']}/itineraries/solo/approve").json()
    assert approved["itineraries"]["solo"]["status"] == "approved"


def test_composition_over_budget_does_not_crash_and_is_flagged_on_swap(client):
    """Edge case: an unreasonably low budget shouldn't crash composition --
    the itinerary is still built and priced from real fixtures; revalidate()
    (exercised via swap) is what surfaces the budget warning."""
    service = _make_service(
        intent_agent=StubAgent(
            lambda p, rf: IntentTurnResult(
                assistant_reply="ok",
                extracted=ExtractedConstraints(
                    origin="JFK", destination="Lisbon",
                    start_date=date(2026, 9, 5), end_date=date(2026, 9, 12),
                    budget_usd=10, adults=1,
                ),
            )
        )
    )
    app.dependency_overrides[get_trip_service] = lambda: service

    session_id = client.post("/sessions").json()["session_id"]
    client.post(f"/sessions/{session_id}/messages", json={"message": "a cheap week in Lisbon"})
    plan = client.post(f"/sessions/{session_id}/compose").json()  # must not 500
    itin_id = plan["candidate_order"][0]
    assert plan["itineraries"][itin_id]["total_cost_usd"] > 10

    swap_resp = client.post(
        f"/plans/{plan['id']}/itineraries/{itin_id}/swap",
        json={
            "component_type": "hotel",
            "component_id": plan["itineraries"][itin_id]["hotel"]["id"],
            "feedback": "x",
        },
    ).json()
    assert any("exceeds your budget" in w for w in swap_resp["warnings"])
