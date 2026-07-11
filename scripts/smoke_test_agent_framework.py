"""Throwaway smoke test for the Microsoft Agent Framework + Azure OpenAI wiring.

Not part of the app -- run manually once to de-risk import paths, auth, and
structured-output/tool-calling shapes before any real app code depends on them.

Usage: python scripts/smoke_test_agent_framework.py
"""

import asyncio
from typing import Annotated

from agent_framework import Agent
from pydantic import BaseModel, Field

from app.llm import get_chat_client


class ToyFact(BaseModel):
    fact: str
    confidence: float


def get_favorite_color(name: Annotated[str, Field(description="A person's name")]) -> str:
    """Look up a person's favorite color."""
    return f"{name}'s favorite color is teal."


async def main() -> None:
    client = get_chat_client()

    print("--- plain run ---")
    agent = Agent(client=client, name="smoke_agent", instructions="Be brief.")
    result = await agent.run("What is the capital of France?")
    print(result)

    print("\n--- structured output (response_format) ---")
    result = await agent.run(
        "State one interesting fact about Lisbon, Portugal, with your confidence from 0 to 1.",
        options={"response_format": ToyFact},
    )
    fact: ToyFact = result.value
    print(fact)
    assert isinstance(fact, ToyFact)

    print("\n--- tool calling + structured output together ---")
    tool_agent = Agent(
        client=client,
        name="smoke_tool_agent",
        instructions="Use the tool to answer. Be brief.",
        tools=[get_favorite_color],
    )
    result = await tool_agent.run(
        "What is Priya's favorite color? Respond as a fact with your confidence.",
        options={"response_format": ToyFact},
    )
    fact = result.value
    print(fact)
    assert "teal" in fact.fact.lower()

    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
