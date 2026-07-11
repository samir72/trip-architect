import asyncio
import os

import pytest
from agent_framework import Agent

from app.llm import get_chat_client
from app.models.constraints import Constraints, PartyComposition
from app.models.itinerary import ActivityOption, DayPlan, FlightOption, HotelOption, Itinerary
from app.models.plan import ItineraryCandidates


def _make_itinerary() -> Itinerary:
    flight = FlightOption(
        id="f1",
        price_usd=500,
        origin="JFK",
        destination="LIS",
        departure_date="2026-09-01",
        return_date="2026-09-07",
        airline="TAP",
        nonstop=True,
        duration_minutes=420,
    )
    hotel = HotelOption(
        id="h1",
        price_usd=900,
        name="Hotel A",
        destination="LIS",
        check_in="2026-09-01",
        check_out="2026-09-07",
        nightly_rate_usd=150,
        tags=["boutique", "walkable"],
    )
    activity = ActivityOption(
        id="a1",
        price_usd=40,
        name="Food tour",
        destination="LIS",
        date="2026-09-02",
        category="food",
    )
    return Itinerary(
        id="i1",
        title="the coastal food trip",
        summary="A walkable, food-forward week in Lisbon.",
        flight=flight,
        hotel=hotel,
        days=[DayPlan(date="2026-09-02", activities=[activity])],
        total_cost_usd=1440,
    )


def test_constraints_missing_fields_all_blank():
    c = Constraints()
    assert set(c.missing_fields()) == {"destination", "dates", "budget", "party"}


def test_constraints_missing_fields_none_when_complete():
    c = Constraints(
        destination="Lisbon",
        start_date="2026-09-01",
        end_date="2026-09-07",
        budget_usd=3000,
        party=PartyComposition(adults=2),
    )
    assert c.missing_fields() == []


def test_itinerary_all_components_and_find_component():
    itin = _make_itinerary()
    ids = {c.id for c in itin.all_components()}
    assert ids == {"f1", "h1", "a1"}
    assert itin.find_component("a1") is not None
    assert itin.find_component("missing") is None


@pytest.mark.skipif(not os.getenv("AZURE_OPENAI_API_KEY"), reason="requires live Azure OpenAI credentials")
def test_itinerary_candidates_structured_output_round_trip():
    """Guards against strict-schema incompatibilities in the real domain
    models (deep nesting, Optional fields, enums) before any agent is built
    on top of them -- catches this at model-definition time, not later."""

    async def run() -> ItineraryCandidates:
        client = get_chat_client()
        agent = Agent(
            client=client,
            name="model_shape_smoke_test",
            instructions=(
                "You invent a single, minimal, plausible one-day trip to satisfy the schema. "
                "Use empty rationale strings and today's date for any date fields."
            ),
        )
        result = await agent.run(
            "Produce exactly 1 itinerary candidate for a 1-day trip from JFK to LIS.",
            options={"response_format": ItineraryCandidates},
        )
        return result.value

    candidates = asyncio.run(run())
    assert isinstance(candidates, ItineraryCandidates)
    assert 1 <= len(candidates.itineraries) <= 3
