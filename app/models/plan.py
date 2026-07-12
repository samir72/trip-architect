from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.models.constraints import Constraints
from app.models.itinerary import ActivityOption, FlightOption, HotelOption, Itinerary


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
    REPAIR_APPLIED = "repair_applied"


class PlanEvent(BaseModel):
    """One entry in a plan's append-only history.

    Undo pops the last event and restores `snapshot_before` for the affected
    itinerary/itineraries (None `itinerary_id` means a plan-wide event, e.g. a
    reject-triggered recompose that replaces the whole candidate set).
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: PlanEventType
    itinerary_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    feedback: str | None = None
    snapshot_before: dict[str, Itinerary] = Field(default_factory=dict)
    # Only set for plan-wide events (COMPOSED, REJECT_RECOMPOSE), which can
    # also change candidate_order and so must be able to restore it on undo.
    candidate_order_before: list[str] | None = None
    # Only set for REPAIR_APPLIED, so undo() can flip the ProposedRepair back
    # to "pending" in addition to restoring the itinerary snapshot.
    repair_id: str | None = None


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    constraints: Constraints
    itineraries: dict[str, Itinerary] = Field(default_factory=dict)
    candidate_order: list[str] = Field(default_factory=list)  # stable display order
    status: PlanStatus = PlanStatus.COMPOSING
    booked_itinerary_id: str | None = None
    events: list[PlanEvent] = Field(default_factory=list)
    proposed_repairs: list[ProposedRepair] = Field(default_factory=list)

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


class RepairReason(str, Enum):
    PRICE_DROP = "price_drop"
    UNAVAILABLE = "unavailable"


class ProposedRepair(BaseModel):
    """A monitoring-detected change to a booked component, proposed for the
    traveler's explicit approval -- never applied automatically.

    `new_itinerary_snapshot` is the full, precomputed replacement itinerary
    at the moment of proposal, not rebuilt at approval time: another admin
    simulate_* action could fire between propose and approve, and the
    traveler must approve exactly what they were shown, not something
    recomputed later. PlanStore.apply_repair() persists this verbatim.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    plan_id: str
    itinerary_id: str
    component_type: Literal["flight", "hotel", "activity"]
    component_id: str
    reason: RepairReason
    new_component: FlightOption | HotelOption | ActivityOption
    new_itinerary_snapshot: Itinerary
    price_delta_usd: float  # new - old; negative = savings
    rationale: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["pending", "approved", "dismissed"] = "pending"


# Identical shape to SwapOutcome (itinerary, diff, warnings) -- an alias
# rather than a duplicate model, since applying a repair and applying a
# swap produce the same kind of result.
RepairOutcome = SwapOutcome
