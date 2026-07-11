# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Trip Architect: a demo-quality AI travel agent. A short conversation elicits
the traveler's constraints, an agent composes 1–3 candidate itineraries from
a small mocked supply catalog, and the traveler can approve, swap a
component, reject with feedback, or undo before anything is "booked."
Supply and booking are entirely mocked (no real flight/hotel APIs, no real
payments) — see README.md for full product framing and known limitations.

Stack: FastAPI + Gradio (mounted on the same app) + Microsoft Agent
Framework (`agent-framework-core` / `agent-framework-openai`) against Azure
OpenAI.

## Commands

Setup:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in AZURE_OPENAI_* values
```

Run the app locally (serves the Gradio UI at `/` and REST API at `/docs`):
```bash
uvicorn app.main:app --reload
```

Run tests (offline, stubbed agents, no credentials needed):
```bash
pytest                                    # full suite
pytest tests/test_plan_store.py           # one file
pytest tests/test_plan_store.py::test_undo_after_swap_restores_previous_component  # one test
```
One test (`test_itinerary_candidates_structured_output_round_trip` in
`tests/test_models.py`) auto-skips unless `AZURE_OPENAI_API_KEY` is set —
it's a live schema-compatibility smoke test, not part of normal runs.

Manual live-agent scripts (require real Azure OpenAI credentials in `.env`;
**must** be run with `PYTHONPATH=.` since they're invoked as file paths, not
modules — `pytest` and `uvicorn app.main:app` do not need this):
```bash
PYTHONPATH=. python scripts/smoke_test_agent_framework.py     # sanity-checks the raw Agent Framework wiring
PYTHONPATH=. python scripts/manual_test_intent_agent.py       # interactive intent conversation
PYTHONPATH=. python scripts/manual_test_composition_agent.py  # one-shot composition
PYTHONPATH=. python scripts/manual_test_swap_agent.py         # one-shot swap + diff/revalidate
PYTHONPATH=. python scripts/manual_test_api_flow.py           # full lifecycle through the real REST API
```
Run these by hand whenever changing agent/prompt behavior — the stubbed test
suite deliberately never calls the real model.

Eval harness (`evals/`; also real Azure OpenAI calls, run by hand, not CI):
```bash
python -m evals.run                        # run the fixed 9-scenario battery once
python -m evals.run --repeat 3              # rerun 3x, report pass RATE per check (one run is noise)
python -m evals.run --compare OLD.json NEW.json   # diff two saved reports (evals/results/*.json, gitignored)
```
Run this whenever changing a prompt in `app/agents/prompts.py` to see
whether it actually helped. Unlike the pytest suite (deterministic logic
only) or the manual scripts (one-off, not comparable), this scores the real
`composition_agent`/`swap_agent` against a fixed battery and gives a number
you can compare across changes. See `evals/scenarios.py` for what's
covered and why each scenario exists.

Docker:
```bash
docker build -t trip-architect:local .
docker run -p 7860:7860 --env-file .env trip-architect:local
```
The same image works for both Hugging Face Spaces (binds 7860, no `PORT`
set) and Render/similar PaaS hosts (binds `$PORT` when set) — see the
Dockerfile's shell-form `CMD`.

## Architecture

### One orchestration layer, two thin callers

`app/services/trip_service.py` (`TripService`, singleton via
`get_trip_service()`) is the **only** place that implements "what happens
when the traveler does X" (start session, send message, compose, approve,
swap, reject, undo, book). `app/api/*.py` (FastAPI routes) and
`app/ui/gradio_app.py` (Gradio Blocks, calling `trip_service` directly
in-process, no HTTP round trip) are both thin wrappers over it. When adding
a new action, add it to `TripService` first, then wire both callers — never
put orchestration logic directly in a route handler or a Gradio callback.

### Three agents, each with a narrow job

`app/agents/{intent_agent,composition_agent,swap_agent}.py`, each a
Microsoft Agent Framework `Agent` built via `app/llm.py`'s shared
`get_chat_client()`. Structured output goes through `response_format` on
`agent.run(..., options={"response_format": SomeModel})` — this can be set
per-call, not just at agent creation. `ItineraryCandidates` exists only
because structured-output schemas must be a single JSON object, not a bare
list; `swap_agent` returns `Itinerary` directly since it's already a single
object.

**The LLM never authors a fact you can compute.** This is the load-bearing
design rule across the whole agent layer:
- Rationale text (`app/supply/rationale.py`) is generated deterministically
  from real component fields (budget delta, matched tags, cancellation
  date), never by the LLM.
- Every component the LLM selects is re-grounded against a fresh
  authoritative supply lookup by id (`app/supply/reconcile.py`'s
  `reconcile_with_supply`/`reconcile_and_price`) before rationale or totals
  are computed — only *which id* is trusted from the LLM's output, never its
  price/dates/tags. This runs after both composition and swap.
- `total_cost_usd` is always recomputed as `sum(component.price_usd for
  component in itinerary.all_components())`, never taken from the LLM.
- Diffs (`app/agents/swap_agent.py`'s `diff_components`) and revalidation
  warnings (`app/supply/revalidate.py`) are computed by comparing
  old/new Pydantic field values directly, never described by the LLM.

When touching agent prompts (`app/agents/prompts.py`), preserve this split:
the LLM's job is selection, identity/summary text, and day-scheduling —
never arithmetic or a specific factual claim about a component.

### Mocked supply

`app/supply/provider.py`'s `search_flights`/`search_hotels`/
`search_activities` read `app/supply/fixtures/*.json` (Lisbon, Kyoto,
Barcelona only) and are passed directly as Agent Framework tool functions
(`tools=[...]` on `composition_agent`/`swap_agent`) — their signatures use
`Annotated[..., Field(description=...)]` because the framework introspects
them to build the function-calling schema. Broader/country-level
destinations (e.g. "Portugal") are resolved to a specific supported city by
the composition agent's prompt, not by the search functions.

### Store: typed event log, not a version stack

`app/store/plan_store.py`'s `PlanStore` is in-memory and process-local
(hence `--workers 1` in the Dockerfile — multiple workers would each get
their own copy of the store). Undo works via an append-only `PlanEvent` log
on each `Plan`, not a per-itinerary version stack, because it has to reverse
both single-itinerary events (swap, approve, book) and plan-wide events
(the initial compose, and reject-triggered recompose, which replaces the
whole candidate set) uniformly. State machine rules enforced in
`PlanStore`, not in the API layer:
- Swapping an *approved* itinerary demotes it back to `proposed` — content
  never changes silently under an "approved" label.
- Reject-with-feedback is plan-level (replaces all candidates), not
  itinerary-level.
- `booked` is a hard lock: `_assert_not_booked` blocks any further
  swap/approve/reject/book on that plan.

### Testing pattern for agents

`tests/test_api_flow.py`'s `StubAgent` duck-types
`agent_framework.Agent.run()` (`async def run(self, prompt, options=None)`
returning an object with `.value`) so `TripService`'s real orchestration,
`PlanStore`'s event log, and the reconcile/rationale/revalidate pipeline all
run for real in tests — only the network call to Azure OpenAI is replaced,
with canned output built from real fixture ids so reconciliation has
something real to look up. Follow this pattern (not mocking) for any new
agent-touching test that isn't meant to hit the live API.
