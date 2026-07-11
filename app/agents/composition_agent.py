from __future__ import annotations

from agent_framework import Agent

from app.agents.prompts import COMPOSITION_AGENT_INSTRUCTIONS
from app.llm import get_chat_client
from app.models.constraints import Constraints
from app.models.itinerary import Itinerary, ItineraryStatus
from app.models.plan import ItineraryCandidates
from app.supply.provider import search_activities, search_flights, search_hotels
from app.supply.reconcile import reconcile_and_price


def build_composition_agent() -> Agent:
    return Agent(
        client=get_chat_client(),
        name="composition_agent",
        instructions=COMPOSITION_AGENT_INSTRUCTIONS,
        tools=[search_flights, search_hotels, search_activities],
    )


def _finalize(itinerary: Itinerary, constraints: Constraints) -> Itinerary:
    reconcile_and_price(itinerary, constraints)
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
