"""Authenticated Responses API, usable locally or inside an ACA sandbox."""

from __future__ import annotations

import argparse
import json
import secrets
from contextvars import ContextVar
from pathlib import Path

from agent_framework_foundry_hosting import ResponsesHostServer
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .agent import create_agent
from .config import RuntimeSettings

_caller_token: ContextVar[str] = ContextVar("caller_workiq_token")


class BearerAuth:
    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self.expected = api_key.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        if scope["type"] == "http":
            headers = [value for name, value in scope["headers"] if name.lower() == b"x-api-key"]
            if len(headers) != 1 or not secrets.compare_digest(headers[0], self.expected):
                await JSONResponse(
                    {"error": {"message": "Unauthorized", "type": "authentication_error"}},
                    status_code=401,
                )(scope, receive, send)
                return
            if scope["path"] == "/readiness" and scope["method"] == "GET":
                await self.app(scope, receive, send)
                return
            if scope["path"] != "/responses" or scope["method"] != "POST":
                await JSONResponse(
                    {"error": {"message": "Only stateless POST /responses is supported."}},
                    status_code=404,
                )(scope, receive, send)
                return
            authorization = [value for name, value in scope["headers"] if name.lower() == b"authorization"]
            token = ""
            if len(authorization) == 1:
                scheme, separator, value = authorization[0].partition(b" ")
                if scheme.lower() == b"bearer" and separator and value and all(33 <= c <= 126 for c in value):
                    token = value.decode("ascii")
            if not token:
                await JSONResponse(
                    {"error": {"message": "A caller Work IQ bearer token is required.",
                               "type": "authentication_error"}},
                    status_code=401, headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
            body = await Request(scope, receive).body()
            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = None
            if (
                not isinstance(payload, dict)
                or payload.get("store") is not False
                or payload.get("background", False) is not False
                or payload.get("previous_response_id")
                or payload.get("conversation")
            ):
                await JSONResponse(
                    {"error": {"message": "Use store:false without background, conversation, "
                               "or previous_response_id; caller tokens are request-scoped."}},
                    status_code=400,
                )(scope, receive, send)
                return

            delivered = False

            async def replay_body() -> Message:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            context = _caller_token.set(token)
            try:
                await self.app(scope, replay_body, send)
            finally:
                _caller_token.reset(context)
            return
        await self.app(scope, receive, send)


def create_server(settings: RuntimeSettings, token_path: Path) -> ResponsesHostServer:
    server = ResponsesHostServer(
        agent=lambda: create_agent(settings, token_path, _caller_token.get()),
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
