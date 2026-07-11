from __future__ import annotations

from agent_framework import Agent

from app.agents.prompts import COMPOSITION_AGENT_INSTRUCTIONS
from app.llm import get_chat_client
from app.models.constraints import Constraints
from app.models.itinerary import ComponentBase, Itinerary, ItineraryStatus
from app.models.plan import ItineraryCandidates
from app.supply.provider import search_activities, search_flights, search_hotels
from app.supply.rationale import apply_rationale


def build_composition_agent() -> Agent:
    return Agent(
        client=get_chat_client(),
        name="composition_agent",
        instructions=COMPOSITION_AGENT_INSTRUCTIONS,
        tools=[search_flights, search_hotels, search_activities],
    )


def _merge_authoritative(component: ComponentBase, authoritative: dict) -> ComponentBase:
    """Overwrite an LLM-selected component's facts with the actual supply
    record for its id (re-validating so date strings coerce properly),
    preserving any fields the record doesn't carry (rationale, and for
    activities, which day it's scheduled)."""
    merged = component.model_dump(mode="python")
    merged.update(authoritative)
    return type(component).model_validate(merged)


def _reconcile_with_supply(itinerary: Itinerary, constraints: Constraints) -> None:
    """Replace every component's fields with the authoritative mocked-supply
    record for its id, so rationale/pricing are never based on something the
    LLM transcribed (or invented) rather than actually returned by search."""
    resolved_destination = itinerary.hotel.destination or itinerary.flight.destination
    adults = constraints.party.adults if constraints.party else 1

    departure_date = (constraints.start_date or itinerary.flight.departure_date).isoformat()
    return_date = (constraints.end_date or itinerary.flight.return_date).isoformat()
    check_in = (constraints.start_date or itinerary.hotel.check_in).isoformat()
    check_out = (constraints.end_date or itinerary.hotel.check_out).isoformat()

    flights_by_id = {
        f["id"]: f
        for f in search_flights(
            constraints.origin or itinerary.flight.origin, resolved_destination, departure_date, return_date, adults=adults
        )
    }
    hotels_by_id = {
        h["id"]: h
        for h in search_hotels(resolved_destination, check_in, check_out, vibe_tags=constraints.vibe_tags)
    }
    activities_by_id = {
        a["id"]: a for a in search_activities(resolved_destination, vibe_tags=constraints.vibe_tags)
    }

    if itinerary.flight.id in flights_by_id:
        itinerary.flight = _merge_authoritative(itinerary.flight, flights_by_id[itinerary.flight.id])
    if itinerary.hotel.id in hotels_by_id:
        itinerary.hotel = _merge_authoritative(itinerary.hotel, hotels_by_id[itinerary.hotel.id])
    for day in itinerary.days:
        day.activities = [
            _merge_authoritative(activity, activities_by_id[activity.id])
            if activity.id in activities_by_id
            else activity
            for activity in day.activities
        ]


def _finalize(itinerary: Itinerary, constraints: Constraints) -> Itinerary:
    _reconcile_with_supply(itinerary, constraints)
    apply_rationale(itinerary, constraints)
    itinerary.total_cost_usd = round(sum(c.price_usd for c in itinerary.all_components()), 2)
    itinerary.status = ItineraryStatus.PROPOSED
    itinerary.version = 1
    return itinerary


def _render_composition_prompt(constraints: Constraints, feedback: str | None) -> str:
    prompt = f"Traveler constraints (JSON): {constraints.model_dump_json(exclude_none=True)}\n"
    if feedback:
        prompt += (
            f"\nThe traveler rejected the previous candidates with this feedback: {feedback!r}\n"
            "Produce a new set of candidates that addresses it."
        )
    return prompt


async def compose_itineraries(agent: Agent, constraints: Constraints, feedback: str | None = None) -> list[Itinerary]:
    prompt = _render_composition_prompt(constraints, feedback)
    result = await agent.run(prompt, options={"response_format": ItineraryCandidates})
    candidates: ItineraryCandidates = result.value
    return [_finalize(itinerary, constraints) for itinerary in candidates.itineraries]
