"""Gradio Blocks UI, mounted onto the FastAPI app in app/main.py.

Calls TripService directly (in-process, no HTTP round trip to its own
backend) -- see app/services/trip_service.py for the single implementation
of what each action actually does.
"""

from __future__ import annotations

import gradio as gr

from app.models.itinerary import Itinerary
from app.models.plan import Plan
from app.services.trip_service import get_trip_service

MAX_CANDIDATES = 3


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
    service = get_trip_service()
    if not plan_id:
        return [*_render_plan_outputs(None), "Nothing to undo yet."]
    try:
        plan = service.undo(plan_id)
    except Exception as exc:  # noqa: BLE001
        plan = service.get_plan(plan_id)
        return [*_render_plan_outputs(plan), f"Undo failed: {exc}"]
    return [*_render_plan_outputs(plan), "Undid the last change."]


def on_book(plan_id: str | None, itinerary_id: str):
    service = get_trip_service()
    if not plan_id or not itinerary_id:
        return [*_render_plan_outputs(None), "Nothing to book yet."]
    try:
        plan = service.book(plan_id, itinerary_id)
    except Exception as exc:  # noqa: BLE001
        plan = service.get_plan(plan_id)
        return [*_render_plan_outputs(plan), f"Booking failed: {exc}"]
    return [*_render_plan_outputs(plan), f"Booked! Confirmation for {plan.itineraries[itinerary_id].title}."]


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

        # render_outputs interleaves per card in the same order _render_plan_outputs emits.
        render_outputs = []
        for i in range(MAX_CANDIDATES):
            render_outputs.extend([groups[i], markdowns[i], dropdowns[i], itin_id_states[i]])

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
                on_book, inputs=[plan_id_state, itin_id_states[i]], outputs=[*render_outputs, status_box]
            )

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
        undo_btn.click(on_undo, inputs=[plan_id_state], outputs=[*render_outputs, status_box])

    return demo


demo = build_gradio_app()
