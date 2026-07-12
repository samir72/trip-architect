"""Shared business logic used by both the REST API and the Gradio UI.

Neither caller implements orchestration itself -- they're thin wrappers
around this one TripService instance, so there is exactly one implementation
of "what happens when the traveler does X."
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache

from agent_framework import Agent

from app.agents.composition_agent import build_composition_agent, compose_itineraries
from app.agents.intent_agent import build_intent_agent, merge_extracted, run_intent_turn
from app.agents.swap_agent import ComponentType, build_swap_agent, diff_components
from app.agents.swap_agent import swap_component as run_swap_component
from app.models.itinerary import Itinerary
from app.models.plan import Plan, PlanStatus, ProposedRepair, RepairOutcome, RepairReason, SwapOutcome
from app.models.session import ChatMessage, ChatRole, SessionState
from app.store.plan_store import PlanStore
from app.store.session_store import SessionStore
from app.supply import provider
from app.supply.monitor import PriceDropDetection, UnavailableDetection, detect_price_drops, detect_unavailable_components
from app.supply.provider import flight_id_prefix
from app.supply.reconcile import reconcile_and_price
from app.supply.revalidate import revalidate

_ROLE_LABEL = {ChatRole.USER: "traveler", ChatRole.ASSISTANT: "agent"}


class TripService:
    def __init__(
        self,
        session_store: SessionStore,
        plan_store: PlanStore,
        intent_agent: Agent,
        composition_agent: Agent,
        swap_agent: Agent,
    ) -> None:
        self.session_store = session_store
        self.plan_store = plan_store
        self.intent_agent = intent_agent
        self.composition_agent = composition_agent
        self.swap_agent = swap_agent

    # --- intent capture -------------------------------------------------

    def start_session(self) -> SessionState:
        return self.session_store.create()

    async def send_message(self, session_id: str, message: str) -> SessionState:
        session = self.session_store.get(session_id)
        history = [(_ROLE_LABEL[m.role], m.content) for m in session.messages]

        result = await run_intent_turn(self.intent_agent, session.constraints, history, message)

        session.constraints = merge_extracted(session.constraints, result.extracted)
        session.constraints_complete = not session.constraints.missing_fields()
        session.messages.append(ChatMessage(role=ChatRole.USER, content=message))
        session.messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=result.assistant_reply))

        return self.session_store.save(session)

    # --- plan composition -------------------------------------------------

    async def compose(self, session_id: str) -> Plan:
        session = self.session_store.get(session_id)
        if not session.constraints_complete:
            raise ValueError("constraints are not complete yet; keep eliciting before composing")

        itineraries = await compose_itineraries(self.composition_agent, session.constraints)

        plan = self.plan_store.create(Plan(constraints=session.constraints))
        self.plan_store.record_composition(plan.id, itineraries)

        session.plan_id = plan.id
        self.session_store.save(session)
        return self.plan_store.get(plan.id)

    def get_plan(self, plan_id: str) -> Plan:
        return self.plan_store.get(plan_id)

    # --- review actions -------------------------------------------------

    def approve(self, plan_id: str, itinerary_id: str) -> Plan:
        return self.plan_store.approve(plan_id, itinerary_id)

    async def swap(
        self, plan_id: str, itinerary_id: str, component_type: ComponentType, component_id: str, feedback: str
    ) -> SwapOutcome:
        plan = self.plan_store.get(plan_id)
        itinerary = self._require_itinerary(plan, itinerary_id)

        new_itinerary, diff = await run_swap_component(
            self.swap_agent, itinerary, component_type, component_id, feedback, plan.constraints
        )
        plan = self.plan_store.swap_component(plan_id, itinerary_id, new_itinerary)
        updated_itinerary = plan.itineraries[itinerary_id]

        warnings = revalidate(updated_itinerary, plan.constraints)
        return SwapOutcome(itinerary=updated_itinerary, diff=diff, warnings=warnings)

    async def reject(self, plan_id: str, feedback: str) -> Plan:
        plan = self.plan_store.get(plan_id)
        new_itineraries = await compose_itineraries(self.composition_agent, plan.constraints, feedback=feedback)
        return self.plan_store.reject_recompose(plan_id, new_itineraries, feedback)

    def undo(self, plan_id: str) -> Plan:
        return self.plan_store.undo(plan_id)

    def book(self, plan_id: str, itinerary_id: str) -> Plan:
        return self.plan_store.book(plan_id, itinerary_id)

    # --- post-booking monitoring -----------------------------------------

    async def check_for_updates(self, plan_id: str) -> list[ProposedRepair]:
        """Deterministic detection (price drops, unavailable components) for
        the booked itinerary, upserting ProposedRepairs -- safe to call on
        every redisplay of a booked plan, not just once. Dedup is load-
        bearing, not cosmetic: it's what stops a fresh (real, billed) LLM
        call from firing on every single page view while a disruption
        repair is still pending."""
        plan = self.plan_store.get(plan_id)
        if plan.status != PlanStatus.BOOKED or plan.booked_itinerary_id is None:
            return []
        itinerary = plan.itineraries[plan.booked_itinerary_id]

        price_drops = detect_price_drops(itinerary, plan.constraints)
        self._dismiss_stale_pending_repairs(plan, RepairReason.PRICE_DROP, {d.component_id for d in price_drops})
        for detection in price_drops:
            self._upsert_price_drop_repair(plan, itinerary, detection)

        unavailable = detect_unavailable_components(itinerary, plan.constraints)
        self._dismiss_stale_pending_repairs(plan, RepairReason.UNAVAILABLE, {d.component_id for d in unavailable})
        for detection in unavailable:
            if self._has_pending_repair(plan, detection.component_id, RepairReason.UNAVAILABLE):
                continue
            await self._propose_unavailable_repair(plan, itinerary, detection)

        return [r for r in plan.proposed_repairs if r.status == "pending"]

    def _dismiss_stale_pending_repairs(
        self, plan: Plan, reason: RepairReason, still_detected_ids: set[str]
    ) -> None:
        """A pending repair whose underlying condition no longer holds (the
        price corrected back, or a closed component reopened) before the
        traveler approved it must not linger -- otherwise approving it later
        would silently apply a price/availability change that isn't real
        anymore. Auto-dismiss rather than leave it stuck."""
        for repair in plan.proposed_repairs:
            if repair.reason == reason and repair.status == "pending" and repair.component_id not in still_detected_ids:
                repair.status = "dismissed"

    async def approve_repair(self, plan_id: str, repair_id: str) -> RepairOutcome:
        plan = self.plan_store.get(plan_id)
        repair = self._require_repair(plan, repair_id)

        old_itinerary = plan.itineraries.get(repair.itinerary_id)
        old_component = old_itinerary.find_component(repair.component_id) if old_itinerary else None
        if old_component is not None and old_component.cancellation_deadline is not None:
            if old_component.cancellation_deadline < date.today():
                self.plan_store.dismiss_repair(plan_id, repair_id)
                raise ValueError("this repair's rebooking window has passed; it's been dismissed")

        plan = self.plan_store.apply_repair(plan_id, repair_id)
        updated_itinerary = plan.itineraries[repair.itinerary_id]
        warnings = revalidate(updated_itinerary, plan.constraints)
        diff = diff_components(old_component, repair.new_component) if old_component else []
        return RepairOutcome(itinerary=updated_itinerary, diff=diff, warnings=warnings)

    def dismiss_repair(self, plan_id: str, repair_id: str) -> Plan:
        return self.plan_store.dismiss_repair(plan_id, repair_id)

    def reset_simulation(self) -> None:
        provider.simulate_reset()

    def simulate_price_change(
        self, plan_id: str, component_type: ComponentType, component_id: str, new_total_price_usd: float
    ) -> None:
        """new_total_price_usd is the component's full price as shown to the
        traveler (matches ComponentBase.price_usd) -- converted here to
        whatever unit the underlying fixture stores (nightly rate for
        hotels, per-passenger base fare for flights), so admin callers never
        need to know that detail."""
        plan = self.plan_store.get(plan_id)
        itinerary = self._booked_itinerary(plan)
        component = itinerary.find_component(component_id)
        if component is None:
            raise ValueError(f"unknown component {component_id!r} on the booked itinerary")
        destination = itinerary.hotel.destination

        if component_type == "hotel":
            nights = max((itinerary.hotel.check_out - itinerary.hotel.check_in).days, 1)
            provider.simulate_hotel_price_change(destination, component_id, new_total_price_usd / nights)
        elif component_type == "flight":
            prefix = flight_id_prefix(itinerary.flight)
            adults = plan.constraints.party.adults if plan.constraints.party else 1
            provider.simulate_flight_price_change(destination, prefix, new_total_price_usd / max(adults, 1))
        else:
            provider.simulate_activity_price_change(destination, component_id, new_total_price_usd)

    def simulate_unavailable(self, plan_id: str, component_type: ComponentType, component_id: str) -> None:
        plan = self.plan_store.get(plan_id)
        self._booked_itinerary(plan)  # validates the plan is booked before touching global supply state
        provider.simulate_unavailable(component_type, component_id)

    def _upsert_price_drop_repair(self, plan: Plan, itinerary: Itinerary, detection: PriceDropDetection) -> None:
        existing = next(
            (
                r
                for r in plan.proposed_repairs
                if r.component_id == detection.component_id
                and r.reason == RepairReason.PRICE_DROP
                and r.status == "pending"
            ),
            None,
        )
        fresh = self._build_price_drop_repair(plan, itinerary, detection)
        if existing is not None:
            existing.new_component = fresh.new_component
            existing.new_itinerary_snapshot = fresh.new_itinerary_snapshot
            existing.price_delta_usd = fresh.price_delta_usd
            existing.rationale = fresh.rationale
        else:
            plan.proposed_repairs.append(fresh)

    def _build_price_drop_repair(
        self, plan: Plan, itinerary: Itinerary, detection: PriceDropDetection
    ) -> ProposedRepair:
        new_itinerary = itinerary.model_copy(deep=True)
        reconcile_and_price(new_itinerary, plan.constraints)
        component = new_itinerary.find_component(detection.component_id)
        assert component is not None
        # New baseline for the next check, once/if this repair is applied.
        component.price_snapshot_usd = component.price_usd

        savings = detection.new_price_usd - detection.old_price_usd  # negative
        rationale = (
            f"Your {detection.component_type}'s price dropped from "
            f"${detection.old_price_usd:,.0f} to ${detection.new_price_usd:,.0f} "
            f"(save ${abs(savings):,.0f})."
        )
        return ProposedRepair(
            plan_id=plan.id,
            itinerary_id=itinerary.id,
            component_type=detection.component_type,
            component_id=detection.component_id,
            reason=RepairReason.PRICE_DROP,
            new_component=component,
            new_itinerary_snapshot=new_itinerary,
            price_delta_usd=savings,
            rationale=rationale,
        )

    async def _propose_unavailable_repair(
        self, plan: Plan, itinerary: Itinerary, detection: UnavailableDetection
    ) -> None:
        old_component = itinerary.find_component(detection.component_id)
        assert old_component is not None
        feedback = (
            f"This {detection.component_type} ({detection.component_id!r}) is no longer available in "
            f"our system and must be replaced -- do not return the same id. Pick the closest match to "
            f"the original by price and by these preferences: {plan.constraints.vibe_tags}."
        )
        try:
            new_itinerary, diff = await run_swap_component(
                self.swap_agent, itinerary, detection.component_type, detection.component_id, feedback, plan.constraints
            )
        except ValueError:
            return  # no replacement currently available -- skip, don't fail the whole check

        id_diff = next((d for d in diff if d.field == "id"), None)
        new_component_id = id_diff.after if id_diff else detection.component_id
        new_component = new_itinerary.find_component(new_component_id)
        assert new_component is not None
        new_component.price_snapshot_usd = new_component.price_usd

        old_price = old_component.price_snapshot_usd or old_component.price_usd
        rationale = (
            f"Your original {detection.component_type} is no longer available. We found a replacement: "
            f"{getattr(new_component, 'name', new_component.id)} (${new_component.price_usd:,.0f})."
        )
        plan.proposed_repairs.append(
            ProposedRepair(
                plan_id=plan.id,
                itinerary_id=itinerary.id,
                component_type=detection.component_type,
                component_id=detection.component_id,
                reason=RepairReason.UNAVAILABLE,
                new_component=new_component,
                new_itinerary_snapshot=new_itinerary,
                price_delta_usd=new_component.price_usd - old_price,
                rationale=rationale,
            )
        )

    def _has_pending_repair(self, plan: Plan, component_id: str, reason: RepairReason) -> bool:
        return any(
            r.component_id == component_id and r.reason == reason and r.status == "pending"
            for r in plan.proposed_repairs
        )

    def _require_repair(self, plan: Plan, repair_id: str) -> ProposedRepair:
        repair = next((r for r in plan.proposed_repairs if r.id == repair_id), None)
        if repair is None:
            raise ValueError(f"unknown repair {repair_id!r} on plan {plan.id!r}")
        return repair

    def _booked_itinerary(self, plan: Plan) -> Itinerary:
        if plan.booked_itinerary_id is None:
            raise ValueError(f"plan {plan.id!r} is not booked")
        return plan.itineraries[plan.booked_itinerary_id]

    def _require_itinerary(self, plan: Plan, itinerary_id: str) -> Itinerary:
        if itinerary_id not in plan.itineraries:
            raise ValueError(f"unknown itinerary {itinerary_id!r} on plan {plan.id!r}")
        return plan.itineraries[itinerary_id]


@lru_cache
def get_trip_service() -> TripService:
    return TripService(
        session_store=SessionStore(),
        plan_store=PlanStore(),
        intent_agent=build_intent_agent(),
        composition_agent=build_composition_agent(),
        swap_agent=build_swap_agent(),
    )
