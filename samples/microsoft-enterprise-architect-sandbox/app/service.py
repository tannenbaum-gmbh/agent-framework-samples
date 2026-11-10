"""Authenticated Responses API, usable locally or inside an ACA sandbox."""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path

from agent_framework_foundry_hosting import ResponsesHostServer
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .agent import create_agent
from .config import RuntimeSettings


class BearerAuth:
    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self.expected = f"Bearer {api_key}".encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        if scope["type"] == "http":
            headers = [value for name, value in scope["headers"] if name.lower() == b"authorization"]
            if len(headers) != 1 or not secrets.compare_digest(headers[0], self.expected):
                await JSONResponse(
                    {"error": {"message": "Unauthorized", "type": "authentication_error"}},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_server(settings: RuntimeSettings, token_path: Path) -> ResponsesHostServer:
    server = ResponsesHostServer(
        agent=lambda: create_agent(settings, token_path),
        configure_observability=None,
        access_log=None,
    )
    server.add_middleware(BearerAuth, api_key=settings.api_key)
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--tokens", type=Path, required=True)
    args = parser.parse_args()
    settings = RuntimeSettings(**json.loads(args.config.read_text(encoding="utf-8")))
    create_server(settings, args.tokens).run(host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
