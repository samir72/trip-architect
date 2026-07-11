"""The fixed eval battery, as data.

Not part of the pytest suite -- these run against the real
composition/swap agents (real Azure OpenAI calls), driven by
`evals/run.py`. Kept as plain dataclasses so adding a scenario is a
one-line addition, not new code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.constraints import Constraints, PartyComposition

SUPPORTED_CITIES = ("Lisbon", "Kyoto", "Barcelona")


@dataclass
class CompositionScenario:
    name: str
    description: str
    constraints: Constraints
    # None means "any of the 3 supported cities is fine" -- only checked
    # for being *a* supported city, not a specific one.
    expected_city: str | None
    expect_budget_warning: bool = False
    # Soft scenarios are run and reported, but don't count toward the
    # overall hard-fail summary -- for cases where "doesn't crash" is the
    # real bar, not a specific deterministic outcome.
    hard: bool = True


@dataclass
class SwapScenario:
    name: str
    description: str
    base_scenario: CompositionScenario
    component_type: Literal["flight", "hotel", "activity"]
    feedback: str
    price_direction: Literal["cheaper", "pricier"] | None = None
    expected_tag: str | None = None


_BASELINE = CompositionScenario(
    name="baseline",
    description="Clear destination, moderate budget, clear vibe tags -- the happy path.",
    constraints=Constraints(
        origin="JFK",
        destination="Portugal",
        start_date="2026-09-05",
        end_date="2026-09-12",
        budget_usd=4000,
        party=PartyComposition(adults=2),
        vibe_tags=["boutique", "walkable", "food-forward"],
    ),
    expected_city="Lisbon",
)

COMPOSITION_SCENARIOS: list[CompositionScenario] = [
    _BASELINE,
    CompositionScenario(
        name="impossible_budget",
        description="Same trip as baseline but a budget no itinerary can realistically fit.",
        constraints=Constraints(
            origin="JFK",
            destination="Portugal",
            start_date="2026-09-05",
            end_date="2026-09-12",
            budget_usd=200,
            party=PartyComposition(adults=2),
            vibe_tags=["boutique", "walkable", "food-forward"],
        ),
        expected_city="Lisbon",
        expect_budget_warning=True,
    ),
    CompositionScenario(
        name="family_with_kids",
        description="Explicit family-friendly non-negotiable -- should not be silently dropped.",
        constraints=Constraints(
            origin="LAX",
            destination="Japan",
            start_date="2026-10-10",
            end_date="2026-10-17",
            budget_usd=6000,
            party=PartyComposition(adults=2, children=2, child_ages=[6, 9]),
            non_negotiables=["family-friendly"],
            vibe_tags=["outdoors", "culture"],
        ),
        expected_city="Kyoto",
    ),
    CompositionScenario(
        name="ambiguous_destination",
        description="No specific city named -- the agent must pick a supported one and say so.",
        constraints=Constraints(
            origin="JFK",
            destination="somewhere relaxing in Europe",
            start_date="2026-09-05",
            end_date="2026-09-12",
            budget_usd=3500,
            party=PartyComposition(adults=2),
            vibe_tags=["walkable", "food-forward"],
        ),
        expected_city=None,
    ),
    CompositionScenario(
        name="conflicting_preferences",
        description="Non-negotiables in tension -- there's no objectively correct resolution, "
        "so this scenario only checks the agent doesn't break.",
        constraints=Constraints(
            origin="JFK",
            destination="Spain",
            start_date="2026-09-05",
            end_date="2026-09-12",
            budget_usd=1200,
            party=PartyComposition(adults=2),
            non_negotiables=["rock-bottom budget", "luxury boutique hotel"],
            vibe_tags=["walkable"],
        ),
        expected_city="Barcelona",
        hard=False,
    ),
    CompositionScenario(
        name="single_traveler_short_trip",
        description="Short 2-night trip, solo traveler -- an edge case on trip length, not just party size.",
        constraints=Constraints(
            origin="ORD",
            destination="Portugal",
            start_date="2026-09-05",
            end_date="2026-09-07",
            budget_usd=1200,
            party=PartyComposition(adults=1),
            vibe_tags=["walkable", "food-forward"],
        ),
        expected_city="Lisbon",
    ),
]

SWAP_SCENARIOS: list[SwapScenario] = [
    SwapScenario(
        name="swap_hotel_cheaper",
        description="Price-driven feedback -- the new hotel should actually be cheaper.",
        base_scenario=_BASELINE,
        component_type="hotel",
        feedback="Something cheaper, even if it's less central.",
        price_direction="cheaper",
    ),
    SwapScenario(
        name="swap_hotel_more_boutique",
        description="Vibe-driven feedback -- checked against what's actually achievable in the fixtures.",
        base_scenario=_BASELINE,
        component_type="hotel",
        feedback="I'd like something more boutique and stylish.",
        expected_tag="boutique",
    ),
    SwapScenario(
        name="swap_activity",
        description="Swapping a list-embedded component (not the singular flight/hotel slots).",
        base_scenario=_BASELINE,
        component_type="activity",
        feedback="Something more outdoorsy instead.",
        expected_tag="outdoors",
    ),
]
