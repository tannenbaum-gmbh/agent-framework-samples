# Copyright (c) Microsoft. All rights reserved.
"""Launch the sandbox-hosted agent for one question."""

from __future__ import annotations

import asyncio
import sys

from .config import get_settings
from .host import SandboxHostedAgent

DEFAULT_QUESTION = "What are three practical uses for ephemeral compute environments?"


async def main() -> int:
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION
    answer = await SandboxHostedAgent(get_settings()).run(question)
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))