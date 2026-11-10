# Copyright (c) Microsoft. All rights reserved.
"""Launch the sandbox-hosted agent for one question, running as its agent identity."""

from __future__ import annotations

import asyncio
import argparse

from .agent import AGENT_NAME
from .config import get_settings
from .host import AgentIdentitySandboxAgent

DEFAULT_QUESTION = (
    "Create an Azure architecture for supplier invoice automation. Apply our internal "
    "guidelines, state assumptions, and cite current Microsoft documentation."
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--name", default=AGENT_NAME)
    parser.add_argument("--version", required=True, help="Version printed by app.create.")
    args = parser.parse_args()
    result = await AgentIdentitySandboxAgent(get_settings()).run(
        args.question, agent_name=args.name, version=args.version
    )
    print(f"Agent identity: {result.agent_identity}")
    print(f"Blueprint:      {result.blueprint}")
    print(f"User-Agent:     {result.user_agent}")
    print()
    print(result.answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
