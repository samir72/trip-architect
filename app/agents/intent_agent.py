from __future__ import annotations

from datetime import date

from agent_framework import Agent
from pydantic import BaseModel, Field

from app.agents.prompts import INTENT_AGENT_INSTRUCTIONS
from app.llm import get_chat_client
from app.models.constraints import Constraints, PartyComposition


class ExtractedConstraints(BaseModel):
    """Structured extraction of whatever the traveler has revealed so far.

    All fields are optional -- the agent only fills in what it's confident
    about this turn; missing fields mean "not mentioned," not "empty."
    """

    origin: str | None = None
    destination: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget_usd: float | None = None
    adults: int | None = None
    children: int | None = None
    child_ages: list[int] = Field(default_factory=list)
    non_negotiables: list[str] = Field(default_factory=list)
    vibe_tags: list[str] = Field(default_factory=list)


class IntentTurnResult(BaseModel):
    assistant_reply: str
    extracted: ExtractedConstraints


def merge_extracted(constraints: Constraints, extracted: ExtractedConstraints) -> Constraints:
    """Fold one turn's extraction into the running Constraints.

    Scalars overwrite (the traveler may be correcting an earlier answer);
    list fields accumulate uniquely across turns rather than being restated.
    """
    updated = constraints.model_copy(deep=True)

    if extracted.origin is not None:
        updated.origin = extracted.origin
    if extracted.destination is not None:
        updated.destination = extracted.destination
    if extracted.start_date is not None:
        updated.start_date = extracted.start_date
    if extracted.end_date is not None:
        updated.end_date = extracted.end_date
    if extracted.budget_usd is not None:
        updated.budget_usd = extracted.budget_usd

    if extracted.adults is not None:
        if extracted.children is not None:
            # Children count was explicitly given this turn, so child_ages is
            # authoritative too (even if empty) -- don't fall back to stale ages.
            children = extracted.children
            child_ages = extracted.child_ages
        else:
            children = updated.party.children if updated.party else 0
            child_ages = updated.party.child_ages if updated.party else []
        updated.party = PartyComposition(adults=extracted.adults, children=children, child_ages=child_ages)

    for tag in extracted.non_negotiables:
        if tag not in updated.non_negotiables:
            updated.non_negotiables.append(tag)
    for tag in extracted.vibe_tags:
        if tag not in updated.vibe_tags:
            updated.vibe_tags.append(tag)

    return updated


def build_intent_agent() -> Agent:
    return Agent(client=get_chat_client(), name="intent_agent", instructions=INTENT_AGENT_INSTRUCTIONS)


def _render_context(constraints: Constraints, history: list[tuple[str, str]]) -> str:
    transcript = "\n".join(f"{role}: {content}" for role, content in history)
    return (
        f"Known constraints so far (JSON): {constraints.model_dump_json()}\n\n"
        f"Conversation so far:\n{transcript}\n"
    )


async def run_intent_turn(
    agent: Agent, constraints: Constraints, history: list[tuple[str, str]], user_message: str
) -> IntentTurnResult:
    """Run one intent-elicitation turn.

    `history` is the prior (role, content) pairs excluding `user_message`,
    which is appended as the newest turn. Stateless across calls by design --
    the full context is re-sent each turn rather than relying on a
    server-side conversation thread, which keeps this simple for a short
    elicitation conversation.
    """
    prompt = f"{_render_context(constraints, history)}\ntraveler: {user_message}"
    result = await agent.run(prompt, options={"response_format": IntentTurnResult})
    return result.value
