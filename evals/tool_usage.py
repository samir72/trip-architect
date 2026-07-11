"""The one place using Microsoft Agent Framework's own eval tooling
(`agent_framework._evaluation` -- marked @experimental upstream).

Confirms the composition/swap agents actually call the search tools
instead of inventing a hotel/flight/activity. Plain-text prompts, no
`response_format` -- this pass only cares about tool usage, not structured
output, so there's no need to reproduce the real prompt-building logic.
"""

from __future__ import annotations

from agent_framework import Agent, LocalEvaluator, evaluate_agent, tool_called_check

from evals.checks import CheckResult


async def check_tool_usage(agent: Agent, prompt: str, tool_names: tuple[str, ...]) -> CheckResult:
    evaluator = LocalEvaluator(tool_called_check(*tool_names, mode="all"))
    results = await evaluate_agent(agent=agent, queries=[prompt], evaluators=evaluator)
    result = results[0]
    detail = f"{result.passed}/{result.total} passed (tools expected: {tool_names})"
    return CheckResult("tool_usage", result.all_passed, detail)
