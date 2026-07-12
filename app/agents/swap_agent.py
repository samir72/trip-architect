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
    old_fields = old.model_dump(mode="json", exclude={"rationale", "price_snapshot_usd"})
    new_fields = new.model_dump(mode="json", exclude={"rationale", "price_snapshot_usd"})
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
    old_itinerary: Itinerary,
    new_itinerary: Itinerary,
    component_type: ComponentType,
    component_id: str,
) -> tuple[str, tuple[int, int] | None]:
    """Locate the swapped-in replacement's id (and, for an activity, its
    (day_index, activity_index) position). The flight and hotel slots are
    unambiguous by field name. For a list-embedded activity, position-based
    lookup is required, not "find an id that wasn't anywhere in the old
    itinerary": a small fixture catalog (e.g. 4 activities for a 7-night
    trip) means the same activity is often scheduled on multiple days, so a
    genuinely swapped-in replacement can be an id that already exists
    elsewhere in the same itinerary -- that would look like "nothing new"
    under a set-difference check and be missed."""
    if component_type == "flight" and new_itinerary.flight.id != component_id:
        return new_itinerary.flight.id, None
    if component_type == "hotel" and new_itinerary.hotel.id != component_id:
        return new_itinerary.hotel.id, None

    if component_type == "activity":
        # Find the (day_index, activity_index) that held component_id in
        # the old itinerary -- consistent with Itinerary.find_component()'s
        # existing first-match-wins semantics for duplicate ids -- and read
        # back whatever now occupies that same slot.
        for day_index, day in enumerate(old_itinerary.days):
            for activity_index, activity in enumerate(day.activities):
                if activity.id == component_id:
                    if day_index < len(new_itinerary.days):
                        new_activities = new_itinerary.days[day_index].activities
                        if activity_index < len(new_activities):
                            candidate_id = new_activities[activity_index].id
                            if candidate_id != component_id:
                                return candidate_id, (day_index, activity_index)
                    break

    # Fallback: covers a flight/hotel echoed back unchanged (no strictly
    # better option existed -- e.g. the current hotel is already the only
    # one in the city with the requested tag), an activity whose day got
    # restructured (SWAP_AGENT_INSTRUCTIONS permits this in rare cases), or
    # any other genuine no-op. Look for an id that's new anywhere in the
    # itinerary; if there isn't one, the swap agent couldn't produce a
    # distinguishable replacement, and callers should hear that honestly
    # rather than being told a swap happened when nothing changed.
    old_ids = {c.id for c in old_itinerary.all_components()}
    candidate = next((c for c in new_itinerary.all_components() if c.id not in old_ids), None)
    if candidate is None:
        raise ValueError(
            f"swap agent did not produce a distinguishable replacement for "
            f"{component_type} {component_id!r}"
        )
    return candidate.id, None


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

    new_component_id, position = _locate_new_component_id(itinerary, new_itinerary, component_type, component_id)
    reconcile_and_price(new_itinerary, constraints)  # ids survive reconcile even if unmatched

    if position is not None:
        # Position is stable across reconcile (it only replaces each slot's
        # object in place, never reorders/adds/removes days or activities),
        # so this avoids re-finding by id -- which, for a target other than
        # day 1, could otherwise silently match an earlier day that happens
        # to share the same (now-authoritative) activity id.
        day_index, activity_index = position
        new_component = new_itinerary.days[day_index].activities[activity_index]
    else:
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
