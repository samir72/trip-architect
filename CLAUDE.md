# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Trip Architect: a demo-quality AI travel agent. A short conversation elicits
the traveler's constraints, an agent composes 1–3 candidate itineraries from
a small mocked supply catalog, and the traveler can approve, swap a
component, reject with feedback, or undo before anything is "booked."
Booking isn't a dead end either: the same agent keeps watching a booked plan
for price drops and disruptions, and proposes (never silently applies)
repairs. Supply and booking are entirely mocked (no real flight/hotel APIs,
no real payments) — see README.md for full product framing and known
limitations.

**Tech stack:** Python 3.13 · FastAPI (Uvicorn) + Gradio `Blocks` mounted on
the same app/port · Microsoft Agent Framework (`agent-framework-core`,
`agent-framework-openai`) against Azure OpenAI (API-key auth via an Azure
AI Foundry resource's OpenAI-compatible endpoint) · **model is not
hardcoded** — set by `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` in `.env`;
currently deployed as `gpt-5.4-nano` · Pydantic v2 /
`pydantic-settings` · static JSON fixtures, no database · in-memory
process-local state, no persistence layer · `pytest`/`pytest-asyncio` +
a hand-rolled `StubAgent` (no live-model tests in CI) · `evals/`, a custom
agent-quality harness (see Commands below) · Docker (`python:3.13-slim`) ·
deployed on Render, Hugging Face Spaces also supported · GitHub (private).
Exact versions: `requirements.txt`. Runtime image: `Dockerfile`.

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
python -m evals.run                        # run the fixed 10-scenario battery once
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

**Non-negotiables must hold for every candidate composition returns, not
just one.** Found as a real bug via `evals/`: with `hotels.json` having
exactly 3 hotels per city and exactly 1 tagged `family-friendly` per city,
the model would satisfy a `family-friendly` non-negotiable for only 1 of 3
candidates, because `COMPOSITION_AGENT_INSTRUCTIONS` had "each candidate
must use a different hotel" as an explicit hard rule but never said
non-negotiables were required for every candidate — the model resolved the
undocumented tension in favor of the rule that *was* explicit. If you edit
this prompt, preserve: the escape valve for genuinely mutually-exclusive
non-negotiables (needed so `conflicting_preferences` doesn't stall trying
to satisfy the impossible), permission to share a hotel across candidates
when a non-negotiable forces it, and the single-resolved-city-per-candidate-set
rule (a related regression surfaced mid-fix: emphasizing "make candidates
distinct" without this rule caused candidates to land in three different
cities instead of three hotel variations within one). Re-run
`python -m evals.run --repeat 3` before/after any change here — see
Commands above.

**Activity ids are not unique within one itinerary — never assume they
are.** Each city's fixture catalog has only ~4 activities
(`app/supply/fixtures/activities.json`); a trip longer than that guarantees
at least one activity repeats across days (pigeonhole principle). This
broke swap detection: `app/agents/swap_agent.py`'s `_locate_new_component_id`
used to find a valid activity swap by checking whether the new id "wasn't
anywhere in the old itinerary" — which fails whenever the genuinely
swapped-in replacement happens to already be scheduled on another day (the
common case, not an edge case, which is why it reproduced on every eval
run). Fixed with position-based lookup: locate the `(day_index,
activity_index)` that held the target id and read back whatever now
occupies that same slot, with a no-op guard (same id at the same slot ≠ a
real change) and the old set-difference kept only as a fallback for the
rare case where the agent restructures days. The same assumption was also
wrong in `evals/checks.py`'s `check_swap_only_target_changed` (fixed
alongside — positional diff, not id-set arithmetic) and in
`swap_component()`'s post-reconcile component lookup (fixed to re-read by
position, not by id, since first-match-wins by id can silently grab an
earlier day's object sharing the same id when the swap target isn't day
1). If you touch any id-based comparison over `Itinerary.all_components()`
or `itinerary.days`, check whether it silently assumes uniqueness.

### Mocked supply

`app/supply/provider.py`'s `search_flights`/`search_hotels`/
`search_activities` read `app/supply/fixtures/*.json` (Lisbon, Kyoto,
Barcelona only) and are passed directly as Agent Framework tool functions
(`tools=[...]` on `composition_agent`/`swap_agent`) — their signatures use
`Annotated[..., Field(description=...)]` because the framework introspects
them to build the function-calling schema. Broader/country-level
destinations (e.g. "Portugal") are resolved to a single specific supported
city by the composition agent's prompt (not by the search functions), and
that same city must be used for every candidate in the set — see the
non-negotiables note below for why that consistency rule exists.

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
  swap/approve/reject/book on that plan. The one deliberate exception is
  `apply_repair`/`dismiss_repair` (see "Post-booking monitoring" below),
  which *requires* `booked` rather than forbidding it.

### Post-booking monitoring

`TripService.check_for_updates()` runs deterministic detection
(`app/supply/monitor.py`'s `detect_price_drops`/`detect_unavailable_components`
— pure functions, no agent knowledge) against a booked plan's itinerary,
auto-called right after `book()` and whenever the Gradio UI redisplays a
booked plan, plus on demand. Detected changes become `ProposedRepair`
records (`app/models/plan.py`) that the traveler approves or dismisses —
`plan_store.apply_repair()` is the one place that mutates a `booked` plan.

- **Per-component price baselines are stomped, never LLM-trusted.**
  `ComponentBase.price_snapshot_usd` is unconditionally overwritten in
  `PlanStore.book()` (every component, not just the itinerary total) and
  again whenever a repair's replacement itinerary is built — same
  never-trust-the-LLM-for-a-fact principle as rationale text. Excluded from
  `swap_agent.diff_components()`'s dump so it never leaks into a diff.
- **A repair's replacement itinerary is precomputed at *propose* time, not
  rebuilt at *approve* time**, and `apply_repair` persists
  `repair.new_itinerary_snapshot` verbatim. Between propose and approve
  another simulated change could fire; recomputing at approval time could
  let the traveler approve something other than what they were shown.
- **Detection upserts by `(component_id, reason)` and prunes stale
  entries.** `check_for_updates` runs on every redisplay of a booked plan,
  so it must update an existing pending repair in place (not append a
  duplicate) and must dismiss a pending repair whose condition no longer
  holds (found via live testing: resetting simulated supply left a stale
  "price dropped" repair listed even after the price reverted). Without
  the dedup, the disruption path in particular would fire a fresh, billed
  LLM call on every page view while a disruption sat unresolved.
- **The unavailable-component path reuses `swap_component()` unmodified**,
  with synthetic feedback that explicitly forbids echoing the same id back
  (`app/services/trip_service.py`'s `_propose_unavailable_repair`) — the
  ordinary swap no-op fallback ("same id is fine if nothing's better") is
  correct for a traveler-initiated swap but wrong here, since the original
  is verified-gone. If the agent still can't find a distinguishable
  replacement, `ValueError` is caught and that repair is simply not
  proposed, rather than failing the whole `check_for_updates` call.
- **Simulated market changes must run inside the live process, never a
  standalone script.** `app/supply/provider.py`'s fixture dicts are
  module-level state loaded once at import time in the single `uvicorn`
  worker; a separate script process mutating them would touch its own,
  invisible copy. `provider.simulate_price_change`/`simulate_unavailable`/
  `simulate_reset` are only ever called from a FastAPI route
  (`app/api/admin.py`) or a Gradio callback — see that file's docstring.
- Flight ids are constructed at search time
  (`f"{id_prefix}-{origin.lower()}"`), not a fixture's own key, so
  `provider.flight_id_prefix()` is the literal inverse — use it, don't
  parse a flight id string anywhere else.

### Testing pattern for agents

`tests/test_api_flow.py`'s `StubAgent` duck-types
`agent_framework.Agent.run()` (`async def run(self, prompt, options=None)`
returning an object with `.value`) so `TripService`'s real orchestration,
`PlanStore`'s event log, and the reconcile/rationale/revalidate pipeline all
run for real in tests — only the network call to Azure OpenAI is replaced,
with canned output built from real fixture ids so reconciliation has
something real to look up. Follow this pattern (not mocking) for any new
agent-touching test that isn't meant to hit the live API.
