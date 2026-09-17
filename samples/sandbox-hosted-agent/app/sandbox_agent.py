# Copyright (c) Microsoft. All rights reserved.
"""Agent Framework process executed inside the sandbox."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.core.credentials import AccessToken


class FileTokenCredential:
    """Serve the short-lived Entra token supplied by the trusted host launcher."""

    def __init__(self, token_path: Path) -> None:
        token = json.loads(token_path.read_text(encoding="utf-8"))
        self._token = AccessToken(token["token"], token["expires_on"])

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return self._token


async def run(request_path: Path, token_path: Path) -> str:
    """Run one request through an agent entirely inside this process."""
    request = json.loads(request_path.read_text(encoding="utf-8"))
    client = FoundryChatClient(
        project_endpoint=request["project_endpoint"],
        model=request["model_deployment"],
        credential=FileTokenCredential(token_path),
    )
    agent = Agent(
        client=client,
        name="sandbox-hosted-agent",
        instructions="Answer the user's question directly and concisely.",
    )
    response = await agent.run(request["question"])
    return str(response)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--token", type=Path, required=True)
    args = parser.parse_args()
    print(await run(args.request, args.token))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))