"""Gradio Blocks UI, mounted onto the FastAPI app in app/main.py.

Calls TripService directly (in-process, no HTTP round trip to its own
backend) -- see app/services/trip_service.py for the single implementation
of what each action actually does.
"""

from __future__ import annotations

import gradio as gr

from app.models.itinerary import Itinerary
from app.models.plan import Plan, PlanStatus, ProposedRepair
from app.services.trip_service import get_trip_service

MAX_CANDIDATES = 3
MAX_REPAIRS = 3


def _format_itinerary_markdown(itinerary: Itinerary) -> str:
    lines = [
        f"### {itinerary.title}",
        itinerary.summary,
        f"**Total: ${itinerary.total_cost_usd:,.0f}**  ·  status: `{itinerary.status.value}`",
        "",
        f"**Flight** -- {itinerary.flight.airline}, "
        f"{'nonstop' if itinerary.flight.nonstop else 'connecting'}, ${itinerary.flight.price_usd:,.0f}",
        f"> {itinerary.flight.rationale}",
        "",
        f"**Hotel** -- {itinerary.hotel.name}, ${itinerary.hotel.price_usd:,.0f}",
        f"> {itinerary.hotel.rationale}",
        "",
        "**Activities**",
    ]
    for day in itinerary.days:
        for activity in day.activities:
            lines.append(
                f"- {day.date.isoformat()}: {activity.name} (${activity.price_usd:,.0f}) -- {activity.rationale}"
            )
    return "\n".join(lines)


def _swap_choices(itinerary: Itinerary) -> list[tuple[str, str]]:
    choices = [
        (f"Flight: {itinerary.flight.airline} (${itinerary.flight.price_usd:,.0f})", f"flight::{itinerary.flight.id}"),
        (f"Hotel: {itinerary.hotel.name} (${itinerary.hotel.price_usd:,.0f})", f"hotel::{itinerary.hotel.id}"),
    ]
    for day in itinerary.days:
        for activity in day.activities:
            choices.append((f"Activity: {activity.name} (${activity.price_usd:,.0f})", f"activity::{activity.id}"))
    return choices


def _booked_component_choices(itinerary: Itinerary) -> list[tuple[str, str]]:
    """Like _swap_choices, but deduped -- an admin simulating a change on a
    catalog item should pick it once, not once per day it happens to be
    scheduled on (small fixture catalogs mean activities repeat across days)."""
    choices = [
        (f"Flight: {itinerary.flight.airline} (${itinerary.flight.price_usd:,.0f})", f"flight::{itinerary.flight.id}"),
        (f"Hotel: {itinerary.hotel.name} (${itinerary.hotel.price_usd:,.0f})", f"hotel::{itinerary.hotel.id}"),
    ]
    seen_activity_ids: set[str] = set()
    for day in itinerary.days:
        for activity in day.activities:
            if activity.id in seen_activity_ids:
                continue
            seen_activity_ids.add(activity.id)
            choices.append((f"Activity: {activity.name} (${activity.price_usd:,.0f})", f"activity::{activity.id}"))
    return choices


def _format_repair_markdown(repair: ProposedRepair) -> str:
    reason_label = "Price drop" if repair.reason.value == "price_drop" else "No longer available"
    return (
        f"**{reason_label}** ({repair.component_type})\n\n"
        f"{repair.rationale}\n\n"
        f"Price change: **${repair.price_delta_usd:+,.0f}**"
    )


def _render_repairs_outputs(repairs: list[ProposedRepair]) -> list:
    """Flat list of MAX_REPAIRS * 3 values: (group visibility, markdown, repair id), padding unused slots."""
    outputs: list = []
    for i in range(MAX_REPAIRS):
        if i < len(repairs):
            repair = repairs[i]
            outputs.extend([gr.update(visible=True), _format_repair_markdown(repair), repair.id])
        else:
            outputs.extend([gr.update(visible=False), "", ""])
    return outputs


def _render_booked_outputs(plan: Plan | None) -> list:
    """(booked-section visibility, markdown, demo-dropdown choices) + the
    MAX_REPAIRS*3 repair-panel outputs, in that order."""
    is_booked = bool(plan and plan.status == PlanStatus.BOOKED and plan.booked_itinerary_id)
    if is_booked:
        itinerary = plan.itineraries[plan.booked_itinerary_id]
        booked_md = _format_itinerary_markdown(itinerary)
        choices = _booked_component_choices(itinerary)
        pending = [r for r in plan.proposed_repairs if r.status == "pending"]
    else:
        booked_md, choices, pending = "", [], []

    return [
        gr.update(visible=is_booked),
        booked_md,
        gr.update(choices=choices, value=None),
        *_render_repairs_outputs(pending),
    ]


def _render_plan_outputs(plan: Plan | None) -> list:
    """Flat list of MAX_CANDIDATES * 4 values: (group visibility, markdown,
    swap-dropdown choices, itinerary id) per card slot, padding unused slots."""
    candidates = plan.candidates() if plan else []
    outputs: list = []
    for i in range(MAX_CANDIDATES):
        if i < len(candidates):
            itinerary = candidates[i]
            outputs.extend(
                [
                    gr.update(visible=True),
                    _format_itinerary_markdown(itinerary),
                    gr.update(choices=_swap_choices(itinerary), value=None),
                    itinerary.id,
                ]
            )
        else:
            outputs.extend([gr.update(visible=False), "", gr.update(choices=[], value=None), ""])
    return outputs


def _messages_view(session) -> list[dict]:
    return [{"role": m.role.value, "content": m.content} for m in session.messages]


async def on_send(message: str, session_id: str | None):
    service = get_trip_service()
    if not session_id:
        session_id = service.start_session().id
    session = await service.send_message(session_id, message)
    ready = session.constraints_complete
    status = "Ready to build your trip." if ready else ""
    return _messages_view(session), session_id, "", gr.update(interactive=ready), status


async def on_compose(session_id: str | None):
    service = get_trip_service()
    if not session_id:
        return [None, *_render_plan_outputs(None), "Start a conversation first."]
    try:
        plan = await service.compose(session_id)
    except ValueError as exc:
        return [None, *_render_plan_outputs(None), str(exc)]
    count = len(plan.candidate_order)
    status = f"Built {count} candidate itinerar{'y' if count == 1 else 'ies'}."
    return [plan.id, *_render_plan_outputs(plan), status]


def on_approve(plan_id: str | None, itinerary_id: str):
    service = get_trip_service()
    if not plan_id or not itinerary_id:
        return [*_render_plan_outputs(None), "Nothing to approve yet."]
    plan = service.approve(plan_id, itinerary_id)
    return [*_render_plan_outputs(plan), f"Approved {plan.itineraries[itinerary_id].title}."]


async def on_swap(plan_id: str | None, itinerary_id: str, swap_choice: str | None, feedback: str):
    service = get_trip_service()
    if not plan_id or not itinerary_id:
        return [*_render_plan_outputs(None), "Nothing to swap yet."]
    if not swap_choice:
        plan = service.get_plan(plan_id)
        return [*_render_plan_outputs(plan), "Pick something to swap first."]

    component_type, component_id = swap_choice.split("::", 1)
    try:
        outcome = await service.swap(plan_id, itinerary_id, component_type, component_id, feedback)
    except Exception as exc:  # noqa: BLE001 -- surfaced to the traveler, not swallowed
        plan = service.get_plan(plan_id)
        return [*_render_plan_outputs(plan), f"Swap failed: {exc}"]

    plan = service.get_plan(plan_id)
    diff_text = "; ".join(f"{d.field}: {d.before} -> {d.after}" for d in outcome.diff) or "no changes"
    warning_text = "; ".join(outcome.warnings) or "none"
    return [*_render_plan_outputs(plan), f"Swapped. Changes: {diff_text}. Warnings: {warning_text}"]


async def on_reject(plan_id: str | None, feedback: str):
    service = get_trip_service()
    if not plan_id:
        return [*_render_plan_outputs(None), "Nothing to rebuild yet."]
    plan = await service.reject(plan_id, feedback)
    return [*_render_plan_outputs(plan), f"Rebuilt candidates based on your feedback: {feedback!r}"]


def on_undo(plan_id: str | None):
    # Outputs include booked_outputs, not just render_outputs: undo can
    # revert a REPAIR_APPLIED event, which mutates the same itinerary the
    # "Booked trip & monitoring" section displays -- without this it goes
    # stale relative to the (correctly reverted) candidate card.
    service = get_trip_service()
    if not plan_id:
        return [*_render_plan_outputs(None), *_render_booked_outputs(None), "Nothing to undo yet."]
    try:
        plan = service.undo(plan_id)
    except Exception as exc:  # noqa: BLE001
        plan = service.get_plan(plan_id)
        return [*_render_plan_outputs(plan), *_render_booked_outputs(plan), f"Undo failed: {exc}"]
    return [*_render_plan_outputs(plan), *_render_booked_outputs(plan), "Undid the last change."]


async def on_book(plan_id: str | None, itinerary_id: str):
    service = get_trip_service()
    if not plan_id or not itinerary_id:
        return [*_render_plan_outputs(None), *_render_booked_outputs(None), "Nothing to book yet."]
    try:
        plan = service.book(plan_id, itinerary_id)
    except Exception as exc:  # noqa: BLE001
        plan = service.get_plan(plan_id)
        return [*_render_plan_outputs(plan), *_render_booked_outputs(plan), f"Booking failed: {exc}"]

    # Simulates "the agent already started watching" -- no background
    # scheduler in this app, so the first check happens right away instead.
    await service.check_for_updates(plan_id)
    plan = service.get_plan(plan_id)
    return [
        *_render_plan_outputs(plan),
        *_render_booked_outputs(plan),
        f"Booked! Confirmation for {plan.itineraries[itinerary_id].title}.",
    ]


async def on_check_for_updates(plan_id: str | None):
    service = get_trip_service()
    if not plan_id:
        return [*_render_booked_outputs(None), "Nothing booked yet."]
    repairs = await service.check_for_updates(plan_id)
    plan = service.get_plan(plan_id)
    status = f"Found {len(repairs)} update(s)." if repairs else "No updates -- everything still looks good."
    return [*_render_booked_outputs(plan), status]


def _parse_component_choice(choice: str) -> tuple[str, str]:
    component_type, component_id = choice.split("::", 1)
    return component_type, component_id


async def on_simulate_price_drop(plan_id: str | None, component_choice: str | None, new_price: float):
    service = get_trip_service()
    if not plan_id or not component_choice:
        plan = service.get_plan(plan_id) if plan_id else None
        return [*_render_booked_outputs(plan), "Pick a component to simulate a price change on first."]
    component_type, component_id = _parse_component_choice(component_choice)
    try:
        service.simulate_price_change(plan_id, component_type, component_id, float(new_price))
    except Exception as exc:  # noqa: BLE001
        plan = service.get_plan(plan_id)
        return [*_render_booked_outputs(plan), f"Simulation failed: {exc}"]
    await service.check_for_updates(plan_id)
    plan = service.get_plan(plan_id)
    return [*_render_booked_outputs(plan), f"Simulated a new price of ${new_price:,.0f} for that component."]


async def on_simulate_unavailable(plan_id: str | None, component_choice: str | None):
    service = get_trip_service()
    if not plan_id or not component_choice:
        plan = service.get_plan(plan_id) if plan_id else None
        return [*_render_booked_outputs(plan), "Pick a component to simulate unavailability on first."]
    component_type, component_id = _parse_component_choice(component_choice)
    try:
        service.simulate_unavailable(plan_id, component_type, component_id)
    except Exception as exc:  # noqa: BLE001
        plan = service.get_plan(plan_id)
        return [*_render_booked_outputs(plan), f"Simulation failed: {exc}"]
    await service.check_for_updates(plan_id)
    plan = service.get_plan(plan_id)
    return [*_render_booked_outputs(plan), "Simulated that component becoming unavailable."]


def on_reset_simulation(plan_id: str | None):
    service = get_trip_service()
    service.reset_simulation()
    plan = service.get_plan(plan_id) if plan_id else None
    return [*_render_booked_outputs(plan), "Reset all simulated price/availability changes."]


async def on_approve_repair(plan_id: str | None, repair_id: str):
    service = get_trip_service()
    if not plan_id or not repair_id:
        return [*_render_plan_outputs(None), *_render_booked_outputs(None), "Nothing to approve yet."]
    try:
        outcome = await service.approve_repair(plan_id, repair_id)
    except Exception as exc:  # noqa: BLE001
        plan = service.get_plan(plan_id)
        return [*_render_plan_outputs(plan), *_render_booked_outputs(plan), f"Repair approval failed: {exc}"]
    plan = service.get_plan(plan_id)
    diff_text = "; ".join(f"{d.field}: {d.before} -> {d.after}" for d in outcome.diff) or "no changes"
    return [*_render_plan_outputs(plan), *_render_booked_outputs(plan), f"Repair applied. Changes: {diff_text}"]


def on_dismiss_repair(plan_id: str | None, repair_id: str):
    service = get_trip_service()
    if not plan_id or not repair_id:
        return [*_render_booked_outputs(None), "Nothing to dismiss yet."]
    plan = service.dismiss_repair(plan_id, repair_id)
    return [*_render_booked_outputs(plan), "Dismissed."]


def build_gradio_app() -> gr.Blocks:
    with gr.Blocks(title="Trip Architect") as demo:
        gr.Markdown(
            "# Trip Architect\n"
            "Tell us the trip you want; approve the trip we build; change anything, anytime."
        )

        session_id_state = gr.State(None)
        plan_id_state = gr.State(None)

        gr.Markdown("## 1. Tell us about your trip")
        chatbot = gr.Chatbot(
            height=300,
            value=[{"role": "assistant", "content": "What trip are you dreaming up?"}],
        )
        msg_box = gr.Textbox(placeholder="e.g. a relaxing week in Portugal with my partner in September", show_label=False)
        compose_btn = gr.Button("Build my trip", interactive=False)

        status_box = gr.Markdown("")

        gr.Markdown("## 2. Review, swap, approve")

        # Pass 1: create all card components first -- each card's click
        # handlers need to update every card's outputs (not just its own),
        # so the full outputs list must exist before any wiring happens.
        groups, markdowns, dropdowns, itin_id_states, feedback_boxes = [], [], [], [], []
        approve_btns, swap_btns, book_btns = [], [], []

        with gr.Row():
            for _ in range(MAX_CANDIDATES):
                with gr.Column(visible=False) as group:
                    markdown = gr.Markdown("")
                    itin_id_state = gr.State("")
                    swap_dropdown = gr.Dropdown(label="Swap a component", choices=[])
                    feedback_box = gr.Textbox(label="What don't you like about it? (optional)", lines=1)
                    with gr.Row():
                        approve_btn = gr.Button("Approve")
                        swap_btn = gr.Button("Swap")
                        book_btn = gr.Button("Book")

                groups.append(group)
                markdowns.append(markdown)
                dropdowns.append(swap_dropdown)
                itin_id_states.append(itin_id_state)
                feedback_boxes.append(feedback_box)
                approve_btns.append(approve_btn)
                swap_btns.append(swap_btn)
                book_btns.append(book_btn)

        with gr.Row():
            reject_feedback = gr.Textbox(label="Not quite right? Tell us what to change", scale=3)
            reject_btn = gr.Button("Rebuild candidates")
            undo_btn = gr.Button("Undo last change")

        gr.Markdown("## 3. Booked trip & monitoring")
        with gr.Column(visible=False) as booked_group:
            booked_markdown = gr.Markdown("")
            check_updates_btn = gr.Button("Check for updates")

            gr.Markdown(
                "#### Demo controls\n"
                "Supply is static fixture data -- it never changes on its own. These "
                "buttons simulate a market change so you can see monitoring/repair "
                "proposals in action."
            )
            demo_component_dropdown = gr.Dropdown(label="Component", choices=[])
            demo_new_price = gr.Number(label="New price ($, for the price-drop button)", value=0)
            with gr.Row():
                demo_price_drop_btn = gr.Button("Simulate price drop")
                demo_unavailable_btn = gr.Button("Simulate unavailable")
                demo_reset_btn = gr.Button("Reset simulated changes")

            gr.Markdown("#### Proposed repairs")
            repair_groups, repair_markdowns, repair_id_states = [], [], []
            repair_approve_btns, repair_dismiss_btns = [], []
            for _ in range(MAX_REPAIRS):
                with gr.Column(visible=False) as repair_group:
                    repair_markdown = gr.Markdown("")
                    repair_id_state = gr.State("")
                    with gr.Row():
                        repair_approve_btn = gr.Button("Approve repair")
                        repair_dismiss_btn = gr.Button("Dismiss")
                repair_groups.append(repair_group)
                repair_markdowns.append(repair_markdown)
                repair_id_states.append(repair_id_state)
                repair_approve_btns.append(repair_approve_btn)
                repair_dismiss_btns.append(repair_dismiss_btn)

        # render_outputs interleaves per card in the same order _render_plan_outputs emits.
        render_outputs = []
        for i in range(MAX_CANDIDATES):
            render_outputs.extend([groups[i], markdowns[i], dropdowns[i], itin_id_states[i]])

        # booked_outputs interleaves in the same order _render_booked_outputs emits.
        booked_outputs = [booked_group, booked_markdown, demo_component_dropdown]
        for i in range(MAX_REPAIRS):
            booked_outputs.extend([repair_groups[i], repair_markdowns[i], repair_id_states[i]])

        # Pass 2: wire every card's buttons against the complete outputs list.
        for i in range(MAX_CANDIDATES):
            approve_btns[i].click(
                on_approve, inputs=[plan_id_state, itin_id_states[i]], outputs=[*render_outputs, status_box]
            )
            swap_btns[i].click(
                on_swap,
                inputs=[plan_id_state, itin_id_states[i], dropdowns[i], feedback_boxes[i]],
                outputs=[*render_outputs, status_box],
            )
            book_btns[i].click(
                on_book,
                inputs=[plan_id_state, itin_id_states[i]],
                outputs=[*render_outputs, *booked_outputs, status_box],
            )

        for i in range(MAX_REPAIRS):
            repair_approve_btns[i].click(
                on_approve_repair,
                inputs=[plan_id_state, repair_id_states[i]],
                outputs=[*render_outputs, *booked_outputs, status_box],
            )
            repair_dismiss_btns[i].click(
                on_dismiss_repair, inputs=[plan_id_state, repair_id_states[i]], outputs=[*booked_outputs, status_box]
            )

        check_updates_btn.click(
            on_check_for_updates, inputs=[plan_id_state], outputs=[*booked_outputs, status_box]
        )
        demo_price_drop_btn.click(
            on_simulate_price_drop,
            inputs=[plan_id_state, demo_component_dropdown, demo_new_price],
            outputs=[*booked_outputs, status_box],
        )
        demo_unavailable_btn.click(
            on_simulate_unavailable,
            inputs=[plan_id_state, demo_component_dropdown],
            outputs=[*booked_outputs, status_box],
        )
        demo_reset_btn.click(on_reset_simulation, inputs=[plan_id_state], outputs=[*booked_outputs, status_box])

        msg_box.submit(
            on_send,
            inputs=[msg_box, session_id_state],
            outputs=[chatbot, session_id_state, msg_box, compose_btn, status_box],
        )
        compose_btn.click(
            on_compose,
            inputs=[session_id_state],
            outputs=[plan_id_state, *render_outputs, status_box],
        )
        reject_btn.click(on_reject, inputs=[plan_id_state, reject_feedback], outputs=[*render_outputs, status_box])
        undo_btn.click(
            on_undo, inputs=[plan_id_state], outputs=[*render_outputs, *booked_outputs, status_box]
        )

    return demo


demo = build_gradio_app()
