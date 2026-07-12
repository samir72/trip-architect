"""In-memory, process-local plan store with a typed event log for undo.

Single-worker deployment only -- state lives in this process's memory, so
running multiple uvicorn workers would silently split traffic across
inconsistent copies of the store (see Dockerfile: --workers 1).
"""

from __future__ import annotations

from app.models.itinerary import Itinerary, ItineraryStatus
from app.models.plan import Plan, PlanEvent, PlanEventType, PlanStatus


class PlanNotFoundError(KeyError):
    pass


class InvalidPlanTransitionError(ValueError):
    pass


class PlanStore:
    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}

    def create(self, plan: Plan) -> Plan:
        self._plans[plan.id] = plan
        return plan

    def get(self, plan_id: str) -> Plan:
        try:
            return self._plans[plan_id]
        except KeyError:
            raise PlanNotFoundError(plan_id) from None

    def record_composition(self, plan_id: str, itineraries: list[Itinerary]) -> Plan:
        """First-time composition. Use reject_recompose for subsequent ones."""
        plan = self.get(plan_id)
        if plan.itineraries:
            raise InvalidPlanTransitionError("plan already has candidates; use reject_recompose")

        plan.events.append(
            PlanEvent(type=PlanEventType.COMPOSED, snapshot_before={}, candidate_order_before=[])
        )
        for itinerary in itineraries:
            plan.itineraries[itinerary.id] = itinerary
            plan.candidate_order.append(itinerary.id)
        plan.status = PlanStatus.REVIEWING
        return plan

    def swap_component(self, plan_id: str, itinerary_id: str, updated_itinerary: Itinerary) -> Plan:
        plan = self.get(plan_id)
        self._assert_not_booked(plan)
        old = self._require_itinerary(plan, itinerary_id)

        plan.events.append(
            PlanEvent(
                type=PlanEventType.SWAP,
                itinerary_id=itinerary_id,
                snapshot_before={itinerary_id: old.model_copy(deep=True)},
            )
        )
        # Content never changes silently under an "approved" label -- a swap
        # demotes an approved itinerary back to proposed, requiring re-approval.
        was_approved = old.status == ItineraryStatus.APPROVED
        updated_itinerary.version = old.version + 1
        updated_itinerary.status = ItineraryStatus.PROPOSED if was_approved else old.status
        plan.itineraries[itinerary_id] = updated_itinerary
        return plan

    def approve(self, plan_id: str, itinerary_id: str) -> Plan:
        plan = self.get(plan_id)
        self._assert_not_booked(plan)
        old = self._require_itinerary(plan, itinerary_id)

        plan.events.append(
            PlanEvent(
                type=PlanEventType.APPROVE,
                itinerary_id=itinerary_id,
                snapshot_before={itinerary_id: old.model_copy(deep=True)},
            )
        )
        plan.itineraries[itinerary_id] = old.model_copy(update={"status": ItineraryStatus.APPROVED})
        return plan

    def reject_recompose(self, plan_id: str, new_itineraries: list[Itinerary], feedback: str) -> Plan:
        """Plan-level: replaces the whole candidate set. The old set stays in
        the event log so undo can restore it."""
        plan = self.get(plan_id)
        self._assert_not_booked(plan)

        snapshot = {iid: itin.model_copy(deep=True) for iid, itin in plan.itineraries.items()}
        plan.events.append(
            PlanEvent(
                type=PlanEventType.REJECT_RECOMPOSE,
                feedback=feedback,
                snapshot_before=snapshot,
                candidate_order_before=list(plan.candidate_order),
            )
        )
        plan.itineraries = {itin.id: itin for itin in new_itineraries}
        plan.candidate_order = [itin.id for itin in new_itineraries]
        return plan

    def book(self, plan_id: str, itinerary_id: str) -> Plan:
        plan = self.get(plan_id)
        self._assert_not_booked(plan)
        itinerary = self._require_itinerary(plan, itinerary_id)
        if itinerary.status != ItineraryStatus.APPROVED:
            raise InvalidPlanTransitionError("itinerary must be approved before booking")

        plan.events.append(
            PlanEvent(
                type=PlanEventType.BOOK,
                itinerary_id=itinerary_id,
                snapshot_before={itinerary_id: itinerary.model_copy(deep=True)},
            )
        )
        booked = itinerary.model_copy(deep=True)
        booked.status = ItineraryStatus.BOOKED
        booked.price_snapshot_usd = booked.total_cost_usd
        # Per-component snapshots too, not just the itinerary total -- this
        # is what lets app/supply/monitor.py detect *which* component
        # dropped later, not just that the trip total changed.
        booked.flight.price_snapshot_usd = booked.flight.price_usd
        booked.hotel.price_snapshot_usd = booked.hotel.price_usd
        for day in booked.days:
            for activity in day.activities:
                activity.price_snapshot_usd = activity.price_usd
        plan.itineraries[itinerary_id] = booked
        plan.status = PlanStatus.BOOKED
        plan.booked_itinerary_id = itinerary_id
        return plan

    def apply_repair(self, plan_id: str, repair_id: str) -> Plan:
        """The one deliberate, narrow exception to the booked lock: applying
        a repair the traveler has explicitly approved. Persists
        `repair.new_itinerary_snapshot` verbatim -- it was fully precomputed
        at proposal time, not rebuilt here, so the traveler approves exactly
        what they were shown even if other admin actions happened between
        propose and approve."""
        plan = self.get(plan_id)
        if plan.status != PlanStatus.BOOKED:
            raise InvalidPlanTransitionError("plan is not booked; no repair to apply")
        repair = next((r for r in plan.proposed_repairs if r.id == repair_id), None)
        if repair is None or repair.status != "pending":
            raise InvalidPlanTransitionError(f"no pending repair {repair_id!r}")

        old = self._require_itinerary(plan, repair.itinerary_id)
        plan.events.append(
            PlanEvent(
                type=PlanEventType.REPAIR_APPLIED,
                itinerary_id=repair.itinerary_id,
                repair_id=repair.id,
                snapshot_before={repair.itinerary_id: old.model_copy(deep=True)},
            )
        )
        new_itinerary = repair.new_itinerary_snapshot.model_copy(deep=True)
        new_itinerary.version = old.version + 1
        plan.itineraries[repair.itinerary_id] = new_itinerary
        repair.status = "approved"
        return plan

    def dismiss_repair(self, plan_id: str, repair_id: str) -> Plan:
        plan = self.get(plan_id)
        repair = next((r for r in plan.proposed_repairs if r.id == repair_id), None)
        if repair is None or repair.status != "pending":
            raise InvalidPlanTransitionError(f"no pending repair {repair_id!r}")
        repair.status = "dismissed"
        return plan

    def undo(self, plan_id: str) -> Plan:
        """Pop the last event and restore its snapshot_before. Handles
        swap/approve/book (single itinerary) and composed/reject_recompose
        (whole candidate set) uniformly."""
        plan = self.get(plan_id)
        if not plan.events:
            raise InvalidPlanTransitionError("nothing to undo")
        event = plan.events.pop()

        if event.type in (PlanEventType.COMPOSED, PlanEventType.REJECT_RECOMPOSE):
            plan.itineraries = {
                iid: itin.model_copy(deep=True) for iid, itin in event.snapshot_before.items()
            }
            plan.candidate_order = list(event.candidate_order_before or [])
            plan.status = PlanStatus.REVIEWING if plan.itineraries else PlanStatus.COMPOSING
            return plan

        assert event.itinerary_id is not None
        plan.itineraries[event.itinerary_id] = event.snapshot_before[event.itinerary_id]
        if event.type == PlanEventType.BOOK:
            plan.status = PlanStatus.REVIEWING
            plan.booked_itinerary_id = None
        if event.type == PlanEventType.REPAIR_APPLIED and event.repair_id:
            repair = next((r for r in plan.proposed_repairs if r.id == event.repair_id), None)
            if repair is not None:
                repair.status = "pending"
        return plan

    def _require_itinerary(self, plan: Plan, itinerary_id: str) -> Itinerary:
        if itinerary_id not in plan.itineraries:
            raise InvalidPlanTransitionError(f"unknown itinerary {itinerary_id}")
        return plan.itineraries[itinerary_id]

    def _assert_not_booked(self, plan: Plan) -> None:
        if plan.status == PlanStatus.BOOKED:
            raise InvalidPlanTransitionError("plan is booked; no further changes allowed")
