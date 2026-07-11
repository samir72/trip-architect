from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.constraints import Constraints
from app.models.itinerary import Itinerary


class PlanStatus(str, Enum):
    COMPOSING = "composing"
    REVIEWING = "reviewing"
    BOOKED = "booked"


class PlanEventType(str, Enum):
    COMPOSED = "composed"
    SWAP = "swap"
    APPROVE = "approve"
    UNAPPROVE = "unapprove"
    REJECT_RECOMPOSE = "reject_recompose"
    UNDO = "undo"
    BOOK = "book"


class PlanEvent(BaseModel):
    """One entry in a plan's append-only history.

    Undo pops the last event and restores `snapshot_before` for the affected
    itinerary/itineraries (None `itinerary_id` means a plan-wide event, e.g. a
    reject-triggered recompose that replaces the whole candidate set).
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: PlanEventType
    itinerary_id: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    feedback: str | None = None
    snapshot_before: dict[str, Itinerary] = Field(default_factory=dict)


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    constraints: Constraints
    itineraries: dict[str, Itinerary] = Field(default_factory=dict)
    candidate_order: list[str] = Field(default_factory=list)  # stable display order
    status: PlanStatus = PlanStatus.COMPOSING
    booked_itinerary_id: str | None = None
    events: list[PlanEvent] = Field(default_factory=list)

    def candidates(self) -> list[Itinerary]:
        return [self.itineraries[i] for i in self.candidate_order if i in self.itineraries]


class ItineraryCandidates(BaseModel):
    """Structured-output wrapper for the composition agent.

    response_format needs a single JSON object schema, not a bare list.
    """

    itineraries: list[Itinerary] = Field(min_length=1, max_length=3)


class DiffEntry(BaseModel):
    field: str
    before: str
    after: str


class SwapOutcome(BaseModel):
    """API-level result of a swap: the agent's updated itinerary plus
    deterministically computed diff/warnings (never authored by the LLM)."""

    itinerary: Itinerary
    diff: list[DiffEntry]
    warnings: list[str]
