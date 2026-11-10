# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import rsa
from microsoft_agents.activity import Activity, TokenResponse
from microsoft_agents.hosting.core import AgentAuthConfiguration, AuthTypes, TurnContext, TurnState
from microsoft_agents.hosting.aiohttp import CloudAdapter
from microsoft_agents.hosting.core.authorization.jwt import JwtTokenValidator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import server  # noqa: E402
from app.config import IdentitySettings, Settings  # noqa: E402

TENANT = "11111111-1111-1111-1111-111111111111"
CHANNEL_APP = "22222222-2222-2222-2222-222222222222"
SETTINGS = Settings(
    foundry_project_endpoint="https://example.test/api/projects/demo",
    foundry_model_deployment="gpt-5",
    subscription_id="sub",
    resource_group="rg",
    sandbox_group="group",
    sandbox_region="westus2",
    sandbox_disk_image="python-3.14",
    identity=IdentitySettings(
        tenant_id=TENANT, blueprint_app_id="blueprint", agent_identity_client_id="agent"
    ),
    auth_mode="obo",
)


def channel_auth(**kwargs: object) -> AgentAuthConfiguration:
    return AgentAuthConfiguration(
        client_id=CHANNEL_APP, tenant_id=TENANT, client_secret="synthetic-local-secret",
        auth_type=AuthTypes.client_secret, **kwargs,
    )


def create_app() -> web.Application:
    return server.create_app(
        SETTINGS, channel_auth=channel_auth(), oauth_connection_name="blueprint-user-token", version="7"
    )


def test_http_host_rejects_anonymous_and_malformed_requests_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = AsyncMock(side_effect=AssertionError("Unauthenticated request reached the adapter"))
    monkeypatch.setattr(server, "start_agent_process", dispatch)

    async def exercise() -> None:
        async with TestClient(TestServer(create_app())) as client:
            response = await client.get("/health")
            assert response.status == 200 and await response.json() == {"status": "ok"}
            for headers in [{}, {"Authorization": "Basic not-a-token"}, {"Authorization": "Bearer not.a.jwt"}]:
                response = await client.post("/api/messages", json={"type": "message"}, headers=headers)
                assert response.status == 401
            assert (await client.get("/api/messages")).status == 405
        dispatch.assert_not_awaited()

    asyncio.run(exercise())


@pytest.mark.parametrize("invalid", [None, "audience", "issuer", "expired"])
def test_real_sdk_jwt_validation_protects_activity_dispatch(
    monkeypatch: pytest.MonkeyPatch, invalid: str | None,
) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = jwt.PyJWK.from_json(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    monkeypatch.setattr(
        JwtTokenValidator._jwk_client_manager, "get_signing_key", AsyncMock(return_value=public_key)
    )
    claims = {
        "aud": CHANNEL_APP, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "tid": TENANT, "exp": int(time.time()) + 3600,
    }
    if invalid == "audience":
        claims["aud"] = "unrelated-app"
    elif invalid == "issuer":
        claims["iss"] = "https://untrusted.example"
    elif invalid == "expired":
        claims["exp"] = int(time.time()) - 3600
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})

    async def dispatch(request: web.Request, agent: object, adapter: object) -> web.Response:
        assert request["claims_identity"].claims["aud"] == CHANNEL_APP
        assert (await request.json())["type"] == "message"
        return web.Response(status=202)

    adapter_dispatch = AsyncMock(side_effect=dispatch)
    monkeypatch.setattr(server, "start_agent_process", adapter_dispatch)

    async def exercise() -> None:
        async with TestClient(TestServer(create_app())) as client:
            response = await client.post(
                "/api/messages", json={"type": "message"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status == (202 if invalid is None else 401)
        assert adapter_dispatch.await_count == (1 if invalid is None else 0)

    asyncio.run(exercise())


@pytest.mark.parametrize("token", ["broker-caller-token", None])
def test_message_turn_uses_oauth_user_token_not_activity_fields(
    monkeypatch: pytest.MonkeyPatch, token: str | None,
) -> None:
    app = create_app()
    agent = app[server.AGENT_APP_KEY]
    sandbox = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(answer="Architecture answer")))
    monkeypatch.setattr(server, "AgentIdentitySandboxAgent", lambda settings: sandbox)
    get_token = AsyncMock(return_value=TokenResponse(token=token) if token is not None else None)
    monkeypatch.setattr(agent.auth, "get_token", get_token)
    context = TurnContext(
        CloudAdapter(connection_manager=agent.connection_manager),
        Activity(
            type="message", text="Design a workload", channel_id="msteams",
            conversation={"id": "conversation-7"}, from_property={"id": "untrusted-caller-id"},
            channel_data={"user_assertion": "untrusted-activity-token"},
        ),
    )
    send = AsyncMock()
    monkeypatch.setattr(context, "send_activity", send)

    async def exercise() -> None:
        if not token:
            with pytest.raises(RuntimeError, match="without an access token"):
                await agent.on_message(context, TurnState())
            sandbox.run.assert_not_awaited()
        else:
            await agent.on_message(context, TurnState())
            sandbox.run.assert_awaited_once_with(
                "Design a workload", agent_name=server.AGENT_NAME, version="7",
                user_assertion="broker-caller-token", channel_name="msteams",
                conversation_id="conversation-7",
            )
            send.assert_awaited_once_with("Architecture answer")
        get_token.assert_awaited_once_with(context, server.AUTH_HANDLER)

    asyncio.run(exercise())


def test_empty_message_and_error_notifications_are_explicit_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = create_app()[server.AGENT_APP_KEY]
    get_token = AsyncMock()
    monkeypatch.setattr(agent.auth, "get_token", get_token)
    context = TurnContext(CloudAdapter(connection_manager=agent.connection_manager), Activity(type="message", text=" "))
    send = AsyncMock()
    monkeypatch.setattr(context, "send_activity", send)

    async def exercise() -> None:
        await agent.on_message(context, TurnState())
        get_token.assert_not_awaited()
        await agent.on_error(context, RuntimeError("secret-token-do-not-echo"))
        await agent.sign_in_failure(context, TurnState(), server.AUTH_HANDLER)

    asyncio.run(exercise())
    messages = [call.args[0] for call in send.await_args_list]
    assert len(messages) == 3
    assert "secret-token" not in json.dumps(messages)
    assert "failed" in messages[1] and "Sign-in failed" in messages[2]


def test_host_requires_obo_and_authenticated_same_tenant_channel_configuration() -> None:
    arguments = {"oauth_connection_name": "oauth", "version": "7"}
    with pytest.raises(ValueError, match="AGENT_AUTH_MODE=obo"):
        server.create_app(replace(SETTINGS, auth_mode="s2s"), channel_auth=channel_auth(), **arguments)
    with pytest.raises(ValueError, match="Anonymous"):
        server.create_app(SETTINGS, channel_auth=channel_auth(anonymous_allowed=True), **arguments)
    with pytest.raises(ValueError, match="client ID and tenant ID"):
        server.create_app(SETTINGS, channel_auth=AgentAuthConfiguration(), **arguments)
    with pytest.raises(ValueError, match="configured agent tenant"):
        server.create_app(
            SETTINGS, channel_auth=AgentAuthConfiguration(client_id=CHANNEL_APP, tenant_id="other"), **arguments
        )
    app = create_app()
    assert app["agent_configuration"].VALIDATE_ISSUER is True
    assert app[server.AGENT_APP_KEY].options.long_running_messages is True


@pytest.mark.parametrize("signed_in", [False, True])
def test_sdk_route_requires_caller_sign_in_before_sandbox_execution(
    monkeypatch: pytest.MonkeyPatch, signed_in: bool,
) -> None:
    agent = create_app()[server.AGENT_APP_KEY]
    sandbox = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(answer="answer")))
    monkeypatch.setattr(server, "AgentIdentitySandboxAgent", lambda settings: sandbox)
    monkeypatch.setattr(agent.auth, "get_token", AsyncMock(return_value=TokenResponse(token="broker-token")))
    sign_in = AsyncMock(return_value=SimpleNamespace(sign_in_complete=lambda: signed_in))
    monkeypatch.setattr(agent.auth, "_start_or_continue_sign_in", sign_in)
    context = TurnContext(
        CloudAdapter(connection_manager=agent.connection_manager),
        Activity(
            type="message", text="Design a workload", channel_id="msteams",
            conversation={"id": "conversation-7"},
        ),
    )
    monkeypatch.setattr(context, "send_activity", AsyncMock())
    asyncio.run(agent._on_activity(context, TurnState()))
    sign_in.assert_awaited_once()
    assert sign_in.call_args.args[2] == server.AUTH_HANDLER
    assert sandbox.run.await_count == (1 if signed_in else 0)


def test_host_loads_standard_sdk_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": CHANNEL_APP,
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": TENANT,
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": "synthetic-secret",
        "FOUNDRY_AGENT_VERSION": "7",
        "AGENT_OAUTH_CONNECTION_NAME": "blueprint-user-token",
    }.items():
        monkeypatch.setenv(key, value)
    app = server.create_app(SETTINGS)
    assert app["agent_configuration"].CLIENT_ID == CHANNEL_APP
    assert app["agent_configuration"].ANONYMOUS_ALLOWED is False
