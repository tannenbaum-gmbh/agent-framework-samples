# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx
import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sidecar import OBSERVABILITY_SCOPE, SidecarCredential, SidecarError  # noqa: E402

KEY = "test-signing-key-that-is-long-enough-for-hs256"


def jwt_for(ttl: int = 3600) -> str:
    return jwt.encode({"exp": int(time.time()) + ttl}, KEY, algorithm="HS256")


def credential(handler, user_assertion: str | None = "user-token") -> SidecarCredential:
    client = httpx.AsyncClient(base_url="http://sidecar", transport=httpx.MockTransport(handler))
    return SidecarCredential("agent-app", user_assertion=user_assertion, http_client=client)


def test_delegated_token_uses_obo_endpoint_with_user_assertion_and_caches() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"authorizationHeader": f"Bearer {jwt_for()}"})

    async def scenario() -> None:
        cred = credential(handler)
        first = await cred.get_token("https://ai.azure.com/.default")
        second = await cred.get_token("https://ai.azure.com/.default")
        assert first == second
        await cred.close()

    asyncio.run(scenario())
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/AuthorizationHeader/Agent"
    assert request.headers["Authorization"] == "Bearer user-token"
    assert request.url.params["AgentIdentity"] == "agent-app"
    assert request.url.params.get_list("optionsOverride.Scopes") == ["https://ai.azure.com/.default"]


def test_observability_token_is_app_only_and_sends_no_user_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"authorizationHeader": f"Bearer {jwt_for()}"})

    asyncio.run(credential(handler).get_token(OBSERVABILITY_SCOPE))
    assert requests[0].url.path == "/AuthorizationHeaderUnauthenticated/AgentApp"
    assert "Authorization" not in requests[0].headers


def test_expiring_token_is_refreshed() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"authorizationHeader": f"Bearer {jwt_for(60)}"})

    async def scenario() -> None:
        cred = credential(handler)
        await cred.get_token("scope")
        await cred.get_token("scope")

    asyncio.run(scenario())
    assert calls == 2


def test_delegated_token_requires_user_assertion() -> None:
    with pytest.raises(ValueError, match="user assertion"):
        asyncio.run(credential(lambda r: httpx.Response(500), user_assertion=None).get_token("scope"))


def test_observability_scope_cannot_be_mixed() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        asyncio.run(credential(lambda r: httpx.Response(500)).get_token(OBSERVABILITY_SCOPE, "other"))


@pytest.mark.parametrize(
    "response",
    [httpx.Response(401, text="invalid_token"), httpx.Response(200, json={"authorizationHeader": "Basic abc"})],
)
def test_sidecar_failures_are_reported(response: httpx.Response) -> None:
    with pytest.raises(SidecarError):
        asyncio.run(credential(lambda r: response).get_token("scope"))
