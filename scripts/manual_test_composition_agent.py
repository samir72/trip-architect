"""Manual smoke test for the composition agent against live Azure OpenAI.

Not part of the app or CI. Run: python scripts/manual_test_composition_agent.py
"""

import asyncio

from app.agents.composition_agent import build_composition_agent, compose_itineraries
from app.models.constraints import Constraints, PartyComposition


async def main() -> None:
    constraints = Constraints(
        origin="JFK",
        destination="Portugal",
        start_date="2026-09-05",
        end_date="2026-09-12",
        budget_usd=4000,
        party=PartyComposition(adults=2),
        non_negotiables=["avoid touristy areas"],
        vibe_tags=["boutique", "walkable", "food-forward"],
    )

    agent = build_composition_agent()
    itineraries = await compose_itineraries(agent, constraints)

    print(f"Got {len(itineraries)} candidate(s)\n")
    for itin in itineraries:
        print(f"=== {itin.title} ===")
        print(itin.summary)
        print(f"Total: ${itin.total_cost_usd}  (status={itin.status})")
        print(f"Flight: {itin.flight.airline} {'nonstop' if itin.flight.nonstop else 'connecting'} "
              f"${itin.flight.price_usd} -- {itin.flight.rationale}")
        print(f"Hotel: {itin.hotel.name} ${itin.hotel.price_usd} -- {itin.hotel.rationale}")
        for day in itin.days:
            for activity in day.activities:
                print(f"  {day.date} {activity.name} (${activity.price_usd}) -- {activity.rationale}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
