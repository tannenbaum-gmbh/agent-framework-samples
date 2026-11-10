# Copyright (c) Microsoft. All rights reserved.
"""Command-line launcher: ``up``, ``invoke``, ``refresh``, and ``down``."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import get_settings
from .host import Deployment, SandboxResponsesHost

STATE_FILE = Path(".sandbox-state.json")
DEFAULT_QUESTION = "What are three practical uses for ephemeral compute environments?"


def save_state(deployment: Deployment, path: Path = STATE_FILE) -> None:
    """Write the state file owner-readable only, because it contains the API key."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(deployment.to_json())
    path.chmod(0o600)


def load_state(path: Path = STATE_FILE) -> Deployment:
    if not path.exists():
        raise RuntimeError(f"No deployment state found at {path}; run `python -m app.cli up` first.")
    return Deployment.from_json(path.read_text(encoding="utf-8"))


def output_text(response: dict[str, Any]) -> str:
    """Extract assistant text from a Responses API result, rejecting incomplete results."""
    if response.get("status") != "completed":
        raise RuntimeError(f"Response did not complete: {response.get('status')} {response.get('error')}")
    parts = [
        content.get("text", "")
        for item in response.get("output", [])
        if item.get("type") == "message"
        for content in item.get("content", [])
        if content.get("type") == "output_text"
    ]
    return "".join(parts).strip()


async def invoke(deployment: Deployment, question: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    async with httpx.AsyncClient(transport=transport, timeout=120) as client:
        response = await client.post(
            f"{deployment.url}/responses",
            headers={"Authorization": f"Bearer {deployment.api_key}"},
            json={"input": question, "stream": False, "store": False},
        )
        response.raise_for_status()
        return output_text(response.json())


def _expiry(expires_on: int) -> str:
    return datetime.fromtimestamp(expires_on, tz=timezone.utc).isoformat(timespec="seconds")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("up", help="Create the sandbox and start the Responses endpoint.")
    invoke_parser = commands.add_parser("invoke", help="Send one question to the Responses endpoint.")
    invoke_parser.add_argument("question", nargs="*")
    commands.add_parser("refresh", help="Resume the sandbox if suspended and replace its Foundry token.")
    commands.add_parser("down", help="Delete the sandbox and the local state file.")
    args = parser.parse_args(argv)

    if args.command == "invoke":
        print(await invoke(load_state(), " ".join(args.question) or DEFAULT_QUESTION))
        return 0

    host = SandboxResponsesHost(get_settings())
    if args.command == "up":
        if STATE_FILE.exists():
            raise RuntimeError(f"{STATE_FILE} already exists; run `python -m app.cli down` first.")
        deployment = await host.start()
        save_state(deployment)
        print(f"Sandbox:   {deployment.sandbox_id}")
        print(f"Endpoint:  {deployment.url}/responses")
        print(f"API key:   stored in {STATE_FILE}")
        print(f"Token expires: {_expiry(deployment.token_expires_on)} (run `python -m app.cli refresh` to renew)")
    elif args.command == "refresh":
        deployment = load_state()
        expires_on = await host.refresh(deployment.sandbox_id)
        save_state(Deployment(deployment.sandbox_id, deployment.url, deployment.api_key, expires_on))
        print(f"Token refreshed; expires {_expiry(expires_on)}")
    elif args.command == "down":
        deployment = load_state()
        await host.stop(deployment.sandbox_id)
        STATE_FILE.unlink()
        print(f"Deleted sandbox {deployment.sandbox_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
