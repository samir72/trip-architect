"""CLI entrypoint for the eval harness.

Usage:
    python -m evals.run [--repeat N]
    python -m evals.run --compare OLD.json NEW.json

Runs the real composition_agent/swap_agent against the fixed battery in
evals/scenarios.py, scores results with evals/checks.py and
evals/tool_usage.py, and prints + saves a report. Costs real Azure OpenAI
calls -- not part of CI, run by hand (same convention as
scripts/manual_test_*.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agent_framework import Agent

from app.agents.composition_agent import _render_composition_prompt, build_composition_agent, compose_itineraries
from app.agents.swap_agent import _render_swap_prompt, build_swap_agent
from app.agents.swap_agent import swap_component as run_swap_component
from app.models.itinerary import Itinerary
from evals.checks import (
    CheckResult,
    check_destination_resolved,
    check_distinct_candidates,
    check_grounded_in_supply,
    check_revalidate,
    check_swap_only_target_changed,
    check_swap_price_direction,
    check_swap_tag_if_achievable,
)
from evals.scenarios import COMPOSITION_SCENARIOS, SWAP_SCENARIOS, CompositionScenario, SwapScenario
from evals.tool_usage import check_tool_usage

RESULTS_DIR = Path(__file__).parent / "results"
SEARCH_TOOL_BY_COMPONENT = {"flight": "search_flights", "hotel": "search_hotels", "activity": "search_activities"}


@dataclass
class ScenarioReport:
    name: str
    kind: Literal["composition", "swap"]
    hard: bool
    checks: list[CheckResult]


def _component(itinerary: Itinerary, component_type: str):
    if component_type == "hotel":
        return itinerary.hotel
    if component_type == "flight":
        return itinerary.flight
    return itinerary.days[0].activities[0]


async def run_composition_scenario(
    agent: Agent, scenario: CompositionScenario
) -> tuple[list[CheckResult], list[Itinerary]]:
    checks: list[CheckResult] = []

    # Real prompt-building logic, not an improvised plain-text prompt -- an
    # earlier attempt at this used a vague natural-language sentence and got
    # a false "no tool calls" failure, because the agent asked a
    # clarifying question instead of composing. The real prompt (structured
    # constraints JSON) is what the agent actually sees in production.
    prompt = _render_composition_prompt(scenario.constraints, feedback=None)
    checks.append(await check_tool_usage(agent, prompt, ("search_flights", "search_hotels", "search_activities")))

    itineraries = await compose_itineraries(agent, scenario.constraints)
    checks.append(check_distinct_candidates(itineraries))
    for itinerary in itineraries:
        checks.append(check_destination_resolved(itinerary, scenario.expected_city))
        checks.append(check_grounded_in_supply(itinerary, scenario.constraints))
        checks.append(check_revalidate(itinerary, scenario.constraints, scenario.expect_budget_warning))

    return checks, itineraries


async def run_swap_scenario(agent: Agent, scenario: SwapScenario, base_itineraries: list[Itinerary]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    itinerary = base_itineraries[0]
    old_component = _component(itinerary, scenario.component_type)
    constraints = scenario.base_scenario.constraints

    prompt = _render_swap_prompt(itinerary, scenario.component_type, old_component.id, scenario.feedback, constraints)
    checks.append(await check_tool_usage(agent, prompt, (SEARCH_TOOL_BY_COMPONENT[scenario.component_type],)))

    new_itinerary, diff = await run_swap_component(
        agent, itinerary, scenario.component_type, old_component.id, scenario.feedback, constraints
    )
    id_diff = next((d for d in diff if d.field == "id"), None)
    new_component_id = id_diff.after if id_diff else old_component.id
    new_component = new_itinerary.find_component(new_component_id)

    checks.append(check_swap_only_target_changed(itinerary, new_itinerary, old_component.id, new_component_id))
    if scenario.price_direction:
        checks.append(check_swap_price_direction(old_component, new_component, scenario.price_direction))
    if scenario.expected_tag:
        checks.append(
            check_swap_tag_if_achievable(
                new_component, old_component, scenario.expected_tag, scenario.component_type,
                itinerary.hotel.destination, constraints,
            )
        )
    return checks


async def run_battery(repeat: int) -> list[list[ScenarioReport]]:
    composition_agent = build_composition_agent()
    swap_agent = build_swap_agent()

    all_runs: list[list[ScenarioReport]] = []
    for _ in range(repeat):
        reports: list[ScenarioReport] = []
        base_itineraries_by_scenario: dict[str, list[Itinerary]] = {}

        for scenario in COMPOSITION_SCENARIOS:
            itineraries: list[Itinerary] = []
            try:
                checks, itineraries = await run_composition_scenario(composition_agent, scenario)
            except Exception as exc:  # noqa: BLE001 -- a scenario failing is data, not a reason to abort the battery
                checks = [CheckResult("scenario_error", False, f"{type(exc).__name__}: {exc}")]
            reports.append(ScenarioReport(scenario.name, "composition", scenario.hard, checks))
            base_itineraries_by_scenario[scenario.name] = itineraries

        for scenario in SWAP_SCENARIOS:
            base_itineraries = base_itineraries_by_scenario[scenario.base_scenario.name]
            if not base_itineraries:
                checks = [CheckResult("scenario_error", False, "base composition scenario failed; swap skipped")]
            else:
                try:
                    checks = await run_swap_scenario(swap_agent, scenario, base_itineraries)
                except Exception as exc:  # noqa: BLE001
                    checks = [CheckResult("scenario_error", False, f"{type(exc).__name__}: {exc}")]
            reports.append(ScenarioReport(scenario.name, "swap", True, checks))

        all_runs.append(reports)
    return all_runs


def aggregate(all_runs: list[list[ScenarioReport]]) -> dict[str, dict[str, float]]:
    """{scenario_name: {check_name: pass_rate}}, averaged across repeats."""
    tallies: dict[str, dict[str, list[bool]]] = {}
    for run in all_runs:
        for report in run:
            bucket = tallies.setdefault(report.name, {})
            for check in report.checks:
                bucket.setdefault(check.name, []).append(check.passed)
    return {
        scenario: {check: sum(values) / len(values) for check, values in checks.items()}
        for scenario, checks in tallies.items()
    }


def print_report(all_runs: list[list[ScenarioReport]], aggregated: dict[str, dict[str, float]]) -> bool:
    """Prints the report, returns True if any hard scenario had a check that didn't pass every run."""
    n = len(all_runs)
    print(f"\n=== Eval report ({n} run{'s' if n != 1 else ''}) ===\n")

    hard_by_scenario = {report.name: report.hard for report in all_runs[0]}
    any_hard_fail = False
    for scenario_name, checks in aggregated.items():
        hard = hard_by_scenario[scenario_name]
        print(f"{scenario_name}{'' if hard else ' [soft]'}")
        for check_name, rate in checks.items():
            status = "PASS" if rate == 1.0 else ("FLAKY" if rate > 0 else "FAIL")
            print(f"  {status:6s} {check_name:26s} {rate:.0%}")
            if hard and rate < 1.0:
                any_hard_fail = True
        print()

    print("ALL HARD CHECKS PASSED" if not any_hard_fail else "SOME HARD CHECKS FAILED")
    return any_hard_fail


def save_report(all_runs: list[list[ScenarioReport]], aggregated: dict[str, dict[str, float]]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{timestamp}.json"
    payload = {
        "timestamp": timestamp,
        "aggregated": aggregated,
        "runs": [
            [{"name": r.name, "kind": r.kind, "hard": r.hard, "checks": [asdict(c) for c in r.checks]} for r in run]
            for run in all_runs
        ],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def compare_reports(old_path: Path, new_path: Path) -> None:
    old = json.loads(old_path.read_text())["aggregated"]
    new = json.loads(new_path.read_text())["aggregated"]
    print(f"\n=== Comparing {old_path.name} -> {new_path.name} ===\n")
    scenario_names = sorted(set(old) | set(new))
    changed = False
    for scenario in scenario_names:
        old_checks, new_checks = old.get(scenario, {}), new.get(scenario, {})
        for check in sorted(set(old_checks) | set(new_checks)):
            o, n = old_checks.get(check), new_checks.get(check)
            if o != n:
                changed = True
                print(f"{scenario}.{check}: {o} -> {n}")
    if not changed:
        print("No differences in pass rates.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trip Architect eval harness")
    parser.add_argument("--repeat", type=int, default=1, help="Run the battery N times and report pass rates")
    parser.add_argument("--compare", nargs=2, metavar=("OLD_JSON", "NEW_JSON"))
    args = parser.parse_args()

    if args.compare:
        compare_reports(Path(args.compare[0]), Path(args.compare[1]))
        return

    all_runs = asyncio.run(run_battery(args.repeat))
    aggregated = aggregate(all_runs)
    any_hard_fail = print_report(all_runs, aggregated)
    path = save_report(all_runs, aggregated)
    print(f"\nSaved: {path}")
    raise SystemExit(1 if any_hard_fail else 0)


if __name__ == "__main__":
    main()
