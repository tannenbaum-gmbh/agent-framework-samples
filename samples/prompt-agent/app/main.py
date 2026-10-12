# Copyright (c) Microsoft. All rights reserved.
"""Create a prompt agent and run it from the command line."""

from __future__ import annotations

import asyncio
import os
import sys

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

DEFAULT_PROMPT = "Explain what an AI agent is in one sentence."


def create_agent() -> Agent:
    """Create an agent backed by a Microsoft Foundry model deployment."""
    project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not project_endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT environment variable is required")

    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-5"),
        credential=DefaultAzureCredential(),
    )
    return Agent(
        client=client,
        name="prompt-agent",
        instructions="Answer the user's prompt clearly and concisely.",
    )


async def main() -> None:
    prompt = " ".join(sys.argv[1:]) or DEFAULT_PROMPT
    response = await create_agent().run(prompt)
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
