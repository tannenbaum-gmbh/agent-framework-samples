# Copyright (c) Microsoft. All rights reserved.
"""Responses API server executed inside the sandbox.

This file is uploaded on its own, so it must not import other modules from ``app``.
"""

from __future__ import annotations

import argparse
import hmac
import json
import time
from pathlib import Path
from typing import Any

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError

AGENT_NAME = "sandbox-responses-agent"
INSTRUCTIONS = "You are a helpful assistant. Answer the user's question directly and concisely."
PUBLIC_PATHS = frozenset({"/readiness"})


class FileTokenCredential:
    """Serve the Foundry token supplied by the launcher; re-read so it can be refreshed in place."""

    def __init__(self, token_path: Path) -> None:
        self._token_path = token_path

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        token = json.loads(self._token_path.read_text(encoding="utf-8"))
        if token["expires_on"] <= time.time():
            raise ClientAuthenticationError(
                "The Foundry token supplied to the sandbox has expired; run `python -m app.cli refresh`."
            )
        return AccessToken(token["token"], token["expires_on"])


class ApiKeyMiddleware:
    """Pure-ASGI middleware requiring ``Authorization: Bearer <key>`` or ``api-key: <key>``."""

    def __init__(self, app: Any, *, api_key: str) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._app = app
        self._api_key = api_key.encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") in PUBLIC_PATHS or self._authorized(scope):
            await self._app(scope, receive, send)
            return
        body = b'{"error":{"code":"unauthorized","message":"A valid API key is required."}}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _authorized(self, scope: dict[str, Any]) -> bool:
        headers = dict(scope.get("headers") or [])
        presented = headers.get(b"api-key", b"")
        authorization = headers.get(b"authorization", b"")
        if authorization[:7].lower() == b"bearer ":
            presented = authorization[7:].strip()
        return bool(presented) and hmac.compare_digest(presented, self._api_key)


def create_agent(config: dict[str, Any], credential: FileTokenCredential) -> Agent:
    """Create a fresh agent and model client for one request."""
    return Agent(
        client=FoundryChatClient(
            project_endpoint=config["project_endpoint"],
            model=config["model_deployment"],
            credential=credential,
        ),
        name=AGENT_NAME,
        instructions=INSTRUCTIONS,
    )


def create_server(config: dict[str, Any], credential: FileTokenCredential, api_key: str) -> ResponsesHostServer:
    server = ResponsesHostServer(agent=lambda: create_agent(config, credential))
    server.add_middleware(ApiKeyMiddleware, api_key=api_key)
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--token", type=Path, required=True)
    parser.add_argument("--api-key", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    api_key = args.api_key.read_text(encoding="utf-8").strip()
    server = create_server(config, FileTokenCredential(args.token), api_key)
    server.run(host="0.0.0.0", port=int(config["port"]))


if __name__ == "__main__":
    main()
