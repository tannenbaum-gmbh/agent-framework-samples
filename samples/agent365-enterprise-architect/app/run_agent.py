# Copyright (c) Microsoft. All rights reserved.
"""Launch one sandbox invocation using OBO or app-only agent authentication."""

from __future__ import annotations

import asyncio
import argparse
from pathlib import Path

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
    parser.add_argument(
        "--user-token-file", type=Path,
        help="OBO only: file containing the caller access token, audienced to the blueprint.",
    )
    args = parser.parse_args()
    settings = get_settings()
    if settings.auth_mode == "obo" and args.user_token_file is None:
        parser.error("--user-token-file is required when AGENT_AUTH_MODE=obo")
    if settings.auth_mode == "s2s" and args.user_token_file is not None:
        parser.error("--user-token-file requires AGENT_AUTH_MODE=obo")
    user_assertion = args.user_token_file.read_text(encoding="utf-8").strip() if args.user_token_file else None
    result = await AgentIdentitySandboxAgent(settings).run(
        args.question, agent_name=args.name, version=args.version, user_assertion=user_assertion
    )
    print(f"Agent identity: {result.agent_identity}")
    print(f"Blueprint:      {result.blueprint}")
    print(f"User-Agent:     {result.user_agent}")
    print()
    print(result.answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
