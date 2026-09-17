# Copyright (c) Microsoft. All rights reserved.
"""Ask the sandboxed code agent a single question, to see the sandbox in action.

Usage::

    python -m app.run_agent "How many primes are below 100000?"
"""

from __future__ import annotations

import asyncio
import sys

from .agent import build_agent
from .config import get_settings
from .sandbox import SandboxCodeRunner

DEFAULT_QUESTION = "What is the 200th Fibonacci number, with F(1) = F(2) = 1?"


async def ask(question: str) -> str:
    """Run one question through the agent and return its final answer."""
    settings = get_settings()
    runner = SandboxCodeRunner(settings.sandbox)
    agent = build_agent(settings, runner)
    try:
        response = await agent.run(question)
        return str(response)
    finally:
        # The sandbox is billed while it exists, so always tear it down.
        await runner.aclose()


async def main() -> int:
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION
    print(f"Question: {question}\n")
    print(await ask(question))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
