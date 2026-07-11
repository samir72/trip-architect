"""Manual, interactive smoke test for the intent elicitation agent.

Not part of the app or CI (requires live Azure OpenAI credentials and a
human in the loop). Run: python scripts/manual_test_intent_agent.py
"""

import asyncio

from app.agents.intent_agent import build_intent_agent, merge_extracted, run_intent_turn
from app.models.constraints import Constraints


async def main() -> None:
    agent = build_intent_agent()
    constraints = Constraints()
    history: list[tuple[str, str]] = []

    print("Trip Architect intent capture (manual test). Ctrl+C to quit.\n")
    print("agent: What trip are you dreaming up?")

    while True:
        user_message = input("you: ")
        result = await run_intent_turn(agent, constraints, history, user_message)
        constraints = merge_extracted(constraints, result.extracted)
        history.append(("traveler", user_message))
        history.append(("agent", result.assistant_reply))

        print(f"agent: {result.assistant_reply}")
        print(f"  [constraints so far: {constraints.model_dump_json(exclude_none=True)}]")
        missing = constraints.missing_fields()
        print(f"  [missing: {missing or 'none -- ready to compose'}]\n")


if __name__ == "__main__":
    asyncio.run(main())
