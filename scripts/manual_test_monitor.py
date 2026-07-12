"""Manual smoke test for post-booking monitoring against live Azure OpenAI.

Not part of the app or CI. Run: python scripts/manual_test_monitor.py
"""

import asyncio

from app.agents.composition_agent import build_composition_agent, compose_itineraries
from app.agents.swap_agent import build_swap_agent
from app.models.constraints import Constraints, PartyComposition
from app.models.plan import Plan
from app.services.trip_service import TripService
from app.store.plan_store import PlanStore
from app.store.session_store import SessionStore
from app.supply import provider


async def main() -> None:
    constraints = Constraints(
        origin="JFK", destination="Portugal", start_date="2026-09-05", end_date="2026-09-12",
        budget_usd=4000, party=PartyComposition(adults=2), vibe_tags=["boutique", "walkable", "food-forward"],
    )

    service = TripService(
        session_store=SessionStore(), plan_store=PlanStore(),
        intent_agent=None, composition_agent=build_composition_agent(), swap_agent=build_swap_agent(),
    )

    itineraries = await compose_itineraries(service.composition_agent, constraints)
    itinerary = itineraries[0]
    plan = service.plan_store.create(Plan(constraints=constraints))
    service.plan_store.record_composition(plan.id, itineraries)
    service.plan_store.approve(plan.id, itinerary.id)
    plan = service.plan_store.book(plan.id, itinerary.id)
    booked = plan.itineraries[itinerary.id]
    print(f"Booked: {booked.title} -- hotel {booked.hotel.name} at ${booked.hotel.price_usd} "
          f"(snapshot ${booked.hotel.price_snapshot_usd})")

    print("\n--- no changes yet: check_for_updates should find nothing ---")
    repairs = await service.check_for_updates(plan.id)
    print(f"pending repairs: {len(repairs)}")
    assert repairs == []

    print("\n--- simulating a price drop on the hotel ---")
    service.simulate_price_change(plan.id, "hotel", booked.hotel.id, new_total_price_usd=booked.hotel.price_usd - 100)
    repairs = await service.check_for_updates(plan.id)
    print(f"pending repairs: {len(repairs)}")
    for r in repairs:
        print(f"  [{r.reason.value}] {r.rationale} (delta: ${r.price_delta_usd:+.0f})")
    assert len(repairs) == 1

    print("\n--- calling check_for_updates again: must NOT duplicate the repair ---")
    repairs_again = await service.check_for_updates(plan.id)
    print(f"pending repairs: {len(repairs_again)}")
    assert len(repairs_again) == 1

    print("\n--- approving the repair ---")
    outcome = await service.approve_repair(plan.id, repairs[0].id)
    print(f"new hotel price: ${outcome.itinerary.hotel.price_usd}")
    print(f"diff: {outcome.diff}")
    print(f"warnings: {outcome.warnings}")

    plan = service.plan_store.get(plan.id)
    print(f"\nplan status after apply_repair: {plan.status.value} (should stay booked)")
    assert plan.status.value == "booked"

    print("\n--- undo: should restore the original price and repair status ---")
    service.plan_store.undo(plan.id)
    plan = service.plan_store.get(plan.id)
    restored = plan.itineraries[itinerary.id]
    repair_status = next(r for r in plan.proposed_repairs if r.id == repairs[0].id).status
    print(f"hotel price after undo: ${restored.hotel.price_usd} (repair status: {repair_status})")

    print("\n--- simulating an unavailable activity ---")
    provider.simulate_reset()
    # Re-fetch: undo restored the pre-repair itinerary.
    plan = service.plan_store.book(plan.id, itinerary.id) if plan.status.value != "booked" else plan
    booked = plan.itineraries[itinerary.id]
    first_activity = booked.days[0].activities[0] if booked.days and booked.days[0].activities else None
    if first_activity:
        service.simulate_unavailable(plan.id, "activity", first_activity.id)
        repairs = await service.check_for_updates(plan.id)
        print(f"pending repairs after simulated unavailability: {len(repairs)}")
        for r in repairs:
            print(f"  [{r.reason.value}] {r.rationale}")
    else:
        print("no activities on this itinerary to test with -- skipped")

    print("\nAll monitor smoke tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
