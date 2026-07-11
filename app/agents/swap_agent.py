from __future__ import annotations

from typing import Literal

from agent_framework import Agent

from app.agents.prompts import SWAP_AGENT_INSTRUCTIONS
from app.llm import get_chat_client
from app.models.constraints import Constraints
from app.models.itinerary import ComponentBase, Itinerary
from app.models.plan import DiffEntry
from app.supply.provider import search_activities, search_flights, search_hotels
from app.supply.reconcile import reconcile_and_price

ComponentType = Literal["flight", "hotel", "activity"]


def build_swap_agent() -> Agent:
    return Agent(
        client=get_chat_client(),
        name="swap_agent",
        instructions=SWAP_AGENT_INSTRUCTIONS,
        tools=[search_flights, search_hotels, search_activities],
    )


def diff_components(old: ComponentBase, new: ComponentBase) -> list[DiffEntry]:
    old_fields = old.model_dump(mode="json", exclude={"rationale"})
    new_fields = new.model_dump(mode="json", exclude={"rationale"})
    return [
        DiffEntry(field=key, before=str(old_fields.get(key)), after=str(new_fields.get(key)))
        for key in sorted(old_fields.keys() | new_fields.keys())
        if old_fields.get(key) != new_fields.get(key)
    ]


def _render_swap_prompt(
    itinerary: Itinerary, component_type: ComponentType, component_id: str, feedback: str, constraints: Constraints
) -> str:
    return (
        f"Traveler constraints (JSON): {constraints.model_dump_json(exclude_none=True)}\n"
        f"Current itinerary (JSON): {itinerary.model_dump_json()}\n"
        f"Swap the {component_type} with id {component_id!r}.\n"
        f"Traveler feedback: {feedback!r}\n"
    )


def _locate_new_component_id(
    old_itinerary: Itinerary, new_itinerary: Itinerary, component_type: ComponentType
) -> str:
    """The swapped-in component has a different real-world id than the one
    it replaced, so it can't be found by the old component_id. The flight
    and hotel slots are unambiguous by field name; for a list-embedded
    activity, the replacement is whichever component appears in the new
    itinerary under an id that wasn't present in the old one."""
    if component_type == "flight":
        return new_itinerary.flight.id
    if component_type == "hotel":
        return new_itinerary.hotel.id

    old_ids = {c.id for c in old_itinerary.all_components()}
    candidate = next((c for c in new_itinerary.all_components() if c.id not in old_ids), None)
    if candidate is None:
        raise ValueError("swap agent did not produce a distinguishable replacement component")
    return candidate.id


async def swap_component(
    agent: Agent,
    itinerary: Itinerary,
    component_type: ComponentType,
    component_id: str,
    feedback: str,
    constraints: Constraints,
) -> tuple[Itinerary, list[DiffEntry]]:
    old_component = itinerary.find_component(component_id)
    if old_component is None:
        raise ValueError(f"unknown component id {component_id!r} on itinerary {itinerary.id!r}")

    prompt = _render_swap_prompt(itinerary, component_type, component_id, feedback, constraints)
    result = await agent.run(prompt, options={"response_format": Itinerary})
    new_itinerary: Itinerary = result.value

    new_component_id = _locate_new_component_id(itinerary, new_itinerary, component_type)
    reconcile_and_price(new_itinerary, constraints)  # ids survive reconcile even if unmatched

    new_component = new_itinerary.find_component(new_component_id)
    assert new_component is not None

    diff = diff_components(old_component, new_component)
    if itinerary.total_cost_usd != new_itinerary.total_cost_usd:
        diff.append(
            DiffEntry(
                field="total_cost_usd",
                before=str(itinerary.total_cost_usd),
                after=str(new_itinerary.total_cost_usd),
            )
        )
    return new_itinerary, diff
