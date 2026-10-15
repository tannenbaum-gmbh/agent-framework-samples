"""Run the shared agent locally without an HTTP host."""

from __future__ import annotations

import argparse
import asyncio

from azure.identity import DefaultAzureCredential

from .agent import DEFAULT_QUESTION, create_agent
from .config import get_settings


async def run(question: str) -> str:
    settings = get_settings()
    with DefaultAzureCredential() as credential:
        async with create_agent(settings, credential) as agent:
            return str(await agent.run(question))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    args = parser.parse_args()
    print(asyncio.run(run(args.question)))


if __name__ == "__main__":
    main()
