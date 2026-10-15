"""Runs the conditional multi-agent workflow from the command line."""

from __future__ import annotations

import argparse
import asyncio

from .config import get_settings
from .workflow import ContentWorkflow


async def _run(prompt: str) -> None:
    result = await ContentWorkflow.from_settings(get_settings()).run(prompt)
    print(result.final_content)
    print(f"\nRevision branch used: {'yes' if result.revised else 'no'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a conditional multi-agent content workflow.")
    parser.add_argument("prompt", help="Content request for the agents to plan, draft, and review.")
    args = parser.parse_args()
    asyncio.run(_run(args.prompt))


if __name__ == "__main__":
    main()