from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class PartyComposition(BaseModel):
    adults: int = Field(ge=1)
    children: int = Field(default=0, ge=0)
    child_ages: list[int] = Field(default_factory=list)


class Constraints(BaseModel):
    origin: str | None = None
    destination: str | None = None  # None means the traveler wants suggestions
    start_date: date | None = None
    end_date: date | None = None
    budget_usd: float | None = None
    party: PartyComposition | None = None
    non_negotiables: list[str] = Field(default_factory=list)
    vibe_tags: list[str] = Field(default_factory=list)  # e.g. "walkable", "boutique", "food-forward"
    notes: str | None = None

    def missing_fields(self) -> list[str]:
        """Fields the intent agent should still ask about before composing.

        Origin and vibe/non-negotiables are optional context, not blockers --
        the agent only withholds composition for the fields that actually
        constrain supply search (destination, dates, budget, party size).
        """
        missing: list[str] = []
        if not self.destination:
            missing.append("destination")
        if not self.start_date or not self.end_date:
            missing.append("dates")
        if self.budget_usd is None:
            missing.append("budget")
        if self.party is None:
            missing.append("party")
        return missing
