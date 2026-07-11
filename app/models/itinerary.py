from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class ItineraryStatus(str, Enum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    BOOKED = "booked"


class ComponentBase(BaseModel):
    id: str
    price_usd: float
    cancellation_deadline: date | None = None
    # Filled in deterministically by app/supply/rationale.py after the
    # composition/swap agent selects a component -- never authored by the LLM.
    rationale: str = ""


class FlightOption(ComponentBase):
    origin: str
    destination: str
    departure_date: date
    return_date: date
    airline: str
    nonstop: bool
    duration_minutes: int


class HotelOption(ComponentBase):
    name: str
    destination: str
    check_in: date
    check_out: date
    nightly_rate_usd: float
    tags: list[str] = Field(default_factory=list)  # e.g. "boutique", "walkable"


class ActivityOption(ComponentBase):
    name: str
    destination: str
    date: date
    category: str  # e.g. "food", "outdoors", "culture"
    tags: list[str] = Field(default_factory=list)


class DayPlan(BaseModel):
    date: date
    activities: list[ActivityOption] = Field(default_factory=list)


class Itinerary(BaseModel):
    id: str
    title: str  # identity, e.g. "the coastal food trip"
    summary: str
    flight: FlightOption
    hotel: HotelOption
    days: list[DayPlan] = Field(default_factory=list)
    total_cost_usd: float
    status: ItineraryStatus = ItineraryStatus.DRAFT
    price_snapshot_usd: float | None = None  # captured at booking time, for a later monitoring phase
    version: int = 1

    def all_components(self) -> list[ComponentBase]:
        components: list[ComponentBase] = [self.flight, self.hotel]
        for day in self.days:
            components.extend(day.activities)
        return components

    def find_component(self, component_id: str) -> ComponentBase | None:
        return next((c for c in self.all_components() if c.id == component_id), None)
