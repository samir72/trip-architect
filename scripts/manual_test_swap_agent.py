"""Manual smoke test for the swap agent against live Azure OpenAI.

Not part of the app or CI. Run: python scripts/manual_test_swap_agent.py
"""

import asyncio

from app.agents.composition_agent import build_composition_agent, compose_itineraries
from app.agents.swap_agent import build_swap_agent, swap_component
from app.models.constraints import Constraints, PartyComposition
from app.supply.revalidate import revalidate


async def main() -> None:
    constraints = Constraints(
        origin="JFK",
        destination="Portugal",
        start_date="2026-09-05",
        end_date="2026-09-12",
        budget_usd=4000,
        party=PartyComposition(adults=2),
        vibe_tags=["boutique", "walkable", "food-forward"],
    )

    composition_agent = build_composition_agent()
    itineraries = await compose_itineraries(composition_agent, constraints)
    itinerary = itineraries[0]

    print(f"Original hotel: {itinerary.hotel.name} (${itinerary.hotel.price_usd}) -- {itinerary.hotel.rationale}")
    print(f"Original total: ${itinerary.total_cost_usd}\n")

    swap_agent = build_swap_agent()
    new_itinerary, diff = await swap_component(
        swap_agent,
        itinerary,
        component_type="hotel",
        component_id=itinerary.hotel.id,
        feedback="Too central and noisy -- I'd rather have a quieter, more budget option even if it's less central.",
        constraints=constraints,
    )

    print(f"New hotel: {new_itinerary.hotel.name} (${new_itinerary.hotel.price_usd}) -- {new_itinerary.hotel.rationale}")
    print(f"New total: ${new_itinerary.total_cost_usd}\n")

    print("Diff:")
    for entry in diff:
        print(f"  {entry.field}: {entry.before!r} -> {entry.after!r}")

    print("\nRevalidation warnings:")
    warnings = revalidate(new_itinerary, constraints)
    print(warnings or "  none")


if __name__ == "__main__":
    asyncio.run(main())
