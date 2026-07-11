"""Shared business logic used by both the REST API and the Gradio UI.

Neither caller implements orchestration itself -- they're thin wrappers
around this one TripService instance, so there is exactly one implementation
of "what happens when the traveler does X."
"""

from __future__ import annotations

from functools import lru_cache

from agent_framework import Agent

from app.agents.composition_agent import build_composition_agent, compose_itineraries
from app.agents.intent_agent import build_intent_agent, merge_extracted, run_intent_turn
from app.agents.swap_agent import ComponentType, build_swap_agent
from app.agents.swap_agent import swap_component as run_swap_component
from app.models.itinerary import Itinerary
from app.models.plan import Plan, SwapOutcome
from app.models.session import ChatMessage, ChatRole, SessionState
from app.store.plan_store import PlanStore
from app.store.session_store import SessionStore
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
