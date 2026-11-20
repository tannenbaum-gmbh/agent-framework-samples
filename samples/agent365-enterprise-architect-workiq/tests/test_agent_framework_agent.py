# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from azure.core.credentials import AccessToken
from microsoft_agents.hosting.core import AuthTypes
from msal import UserAssignedManagedIdentity

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import agent_framework_agent  # noqa: E402


def make_credential() -> agent_framework_agent.AgentIdentityCredential:
    return agent_framework_agent.AgentIdentityCredential("tenant", "blueprint-app", "agent-app", "managed-identity-client")


def test_sdk_configuration_uses_managed_identity_federation() -> None:
    config = make_credential()._connection.configuration
    assert config.AUTH_TYPE == AuthTypes.federated_credentials
    assert config.CLIENT_ID == "blueprint-app"
    assert config.TENANT_ID == "tenant"
    assert config.FEDERATED_CLIENT_ID == "managed-identity-client"
    assert not config.CLIENT_SECRET


def test_sdk_federates_managed_identity_and_sets_fmi_path(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = importlib.import_module("microsoft_agents.authentication.msal.msal_auth")
    calls: dict[str, Any] = {}

    class FakeManagedIdentityClient:
        def __init__(self, identity: Any, **kwargs: Any) -> None:
            calls["identity"] = identity

        def acquire_token_for_client(self, **kwargs: Any) -> dict[str, Any]:
            calls["managed_identity_request"] = kwargs
            return {"access_token": "managed-identity-assertion"}

    class FakeBlueprintApp:
        def __init__(self, **kwargs: Any) -> None:
            calls["blueprint_config"] = kwargs

        def acquire_token_for_client(self, scopes: list[str], **kwargs: Any) -> dict[str, Any]:
            calls["blueprint_request"] = {"scopes": scopes, **kwargs}
            assertion = calls["blueprint_config"]["client_credential"]["client_assertion"]()
            assert assertion == "managed-identity-assertion"
            return {"access_token": "blueprint-exchange-token"}

    monkeypatch.setattr(sdk, "ManagedIdentityClient", FakeManagedIdentityClient)
    monkeypatch.setattr(sdk, "ConfidentialClientApplication", FakeBlueprintApp)
    credential = make_credential()
    token = asyncio.run(credential._connection.get_agentic_application_token("tenant", "agent-app"))
    assert token == "blueprint-exchange-token"
    assert calls["identity"] == UserAssignedManagedIdentity(client_id="managed-identity-client")
    assert calls["managed_identity_request"] == {"resource": "api://AzureADTokenExchange"}
    assert calls["blueprint_config"]["client_id"] == "blueprint-app"
    assert calls["blueprint_request"]["data"] == {"fmi_path": "agent-app"}


def test_agent_token_exchanges_sdk_assertion_for_foundry_token(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = make_credential()
    exchange = AsyncMock(side_effect=["exchange-one", "exchange-two"])
    monkeypatch.setattr(credential._connection, "get_agentic_application_token", exchange)
    calls: list[dict[str, Any]] = []

    class FakeApp:
        def __init__(self, client_id: str, **kwargs: Any) -> None:
            calls.append({"client_id": client_id, **kwargs})

        def acquire_token_for_client(self, scopes: list[str]) -> dict[str, Any]:
            assert scopes == [agent_framework_agent.FOUNDRY_SCOPE]
            return {"access_token": f"agent-token-{len(calls)}", "expires_in": 3600}

    monkeypatch.setattr(agent_framework_agent, "ConfidentialClientApplication", FakeApp)
    monkeypatch.setattr(agent_framework_agent.time, "time", lambda: 1000)

    async def acquire_twice() -> None:
        assert await credential.get_token(agent_framework_agent.FOUNDRY_SCOPE) == AccessToken("agent-token-1", 4600)
        assert await credential.get_token(agent_framework_agent.FOUNDRY_SCOPE) == AccessToken("agent-token-2", 4600)
        await credential.close()

    asyncio.run(acquire_twice())
    assert exchange.await_count == 2
    exchange.assert_awaited_with("tenant", "agent-app")
    assert calls[0]["client_id"] == "agent-app"
    assert calls[0]["authority"] == "https://login.microsoftonline.com/tenant"
    assert calls[0]["client_credential"] == {"client_assertion": "exchange-one"}
    assert calls[1]["client_credential"] == {"client_assertion": "exchange-two"}
    assert calls[0]["token_cache"] is calls[1]["token_cache"]


def test_missing_sdk_assertion_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = make_credential()
    monkeypatch.setattr(credential._connection, "get_agentic_application_token", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match="blueprint token exchange failed"):
        asyncio.run(credential.get_token(agent_framework_agent.FOUNDRY_SCOPE))


def test_managed_identity_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = make_credential()
    monkeypatch.setattr(
        credential._connection, "get_agentic_application_token", AsyncMock(side_effect=RuntimeError("MI unavailable"))
    )
    with pytest.raises(RuntimeError, match="MI unavailable"):
        asyncio.run(credential.get_token(agent_framework_agent.FOUNDRY_SCOPE))


def test_resource_token_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = make_credential()
    monkeypatch.setattr(credential._connection, "get_agentic_application_token", AsyncMock(return_value="assertion"))

    class FailingApp:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def acquire_token_for_client(self, scopes: list[str]) -> dict[str, Any]:
            return {"error": "unauthorized_client", "error_description": "agent disabled"}

    monkeypatch.setattr(agent_framework_agent, "ConfidentialClientApplication", FailingApp)
    with pytest.raises(RuntimeError, match="Agent identity token request failed: agent disabled"):
        asyncio.run(credential.get_token(agent_framework_agent.FOUNDRY_SCOPE))


def test_observability_token_reuses_federation_with_its_own_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = make_credential()
    exchange = AsyncMock(return_value="assertion")
    monkeypatch.setattr(credential._connection, "get_agentic_application_token", exchange)

    class FakeApp:
        def __init__(self, client_id: str, **kwargs: Any) -> None:
            assert client_id == "agent-app"
            assert kwargs["client_credential"] == {"client_assertion": "assertion"}

        def acquire_token_for_client(self, scopes: list[str]) -> dict[str, Any]:
            assert scopes == [agent_framework_agent.OBSERVABILITY_SCOPE]
            return {"access_token": "observability-token", "expires_in": 3600}

    monkeypatch.setattr(agent_framework_agent, "ConfidentialClientApplication", FakeApp)
    token = asyncio.run(credential.get_token(agent_framework_agent.OBSERVABILITY_SCOPE))
    assert token.token == "observability-token"
    exchange.assert_awaited_once_with("tenant", "agent-app")


@pytest.mark.parametrize("scopes", [(), ("https://graph.microsoft.com/.default",)])
def test_credential_rejects_other_scopes(scopes: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="only supports"):
        asyncio.run(make_credential().get_token(*scopes))


def test_system_assigned_identity_uses_no_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import agent_token

    calls: list[str | None] = []

    def assertion_provider(client_id: str | None) -> Any:
        calls.append(client_id)
        return lambda: "system-identity-assertion"

    monkeypatch.setattr(agent_framework_agent, "managed_identity_assertion", assertion_provider)

    def acquire(self: Any, agent_id: str, scope: str) -> tuple[str, int]:
        assert agent_id == "agent"
        assert scope == agent_framework_agent.FOUNDRY_SCOPE
        assert self._client_secret is None
        assert self._assertion_provider() == "system-identity-assertion"
        return "system-agent-token", 4600

    monkeypatch.setattr(agent_token.AgentIdentityTokenProvider, "agent_identity_token", acquire)
    credential = agent_framework_agent.AgentIdentityCredential("tenant", "blueprint", "agent", None)
    token = asyncio.run(credential.get_token(agent_framework_agent.FOUNDRY_SCOPE))
    assert token == AccessToken("system-agent-token", 4600)
    assert calls == [None]


def test_credential_requires_agent_identity_configuration() -> None:
    with pytest.raises(ValueError, match="Tenant, blueprint, and agent client IDs are required"):
        agent_framework_agent.AgentIdentityCredential("tenant", "", "agent", None)


@pytest.mark.parametrize(
    ("agent_id", "blueprint_id", "error"),
    [
        ("agent-app", "blueprint-app", None),
        ("managed-identity-client", "blueprint-app", "expected agent identity"),
        ("agent-app", "other-blueprint", "Token blueprint does not match"),
    ],
)
def test_runtime_acquires_token_and_checks_identity_before_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_id: str, blueprint_id: str, error: str | None
) -> None:
    import agent_framework
    import agent_framework.foundry

    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "tenant_id": "tenant",
                "blueprint_app_id": "blueprint-app",
                "agent_identity_client_id": "agent-app",
                "managed_identity_client_id": "managed-identity-client",
                "project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
                "model_deployment": "gpt-5",
                "question": "hello",
                "user_assertion": "user-token",
                "workiq_enable_actions": True,
            }
        ),
        encoding="utf-8",
    )
    token = jwt.encode(
        {"appid": agent_id, "xms_par_app_azp": blueprint_id},
        "test-signing-key-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )
    acquire = AsyncMock(return_value=AccessToken(token, 2_000_000_000))
    monkeypatch.setattr(agent_framework_agent.DelegatedAgentCredential, "get_token", acquire)
    from app import workiq
    def create_tools(credential: Any, *, enable_actions: bool) -> tuple[list[Any], list[Any]]:
        assert isinstance(credential, agent_framework_agent.DelegatedAgentCredential)
        assert enable_actions is True
        return [], []

    monkeypatch.setattr(workiq, "create_tools", create_tools)
    clients: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            clients.append(kwargs)

    class FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["id"] == "agent-app"

        async def run(self, question: str) -> str:
            assert question == "hello"
            return "answer"

    monkeypatch.setattr(agent_framework.foundry, "FoundryChatClient", FakeClient)
    monkeypatch.setattr(agent_framework, "Agent", FakeAgent)
    if error:
        with pytest.raises(RuntimeError, match=error):
            asyncio.run(agent_framework_agent.run(request_path))
        assert not clients
    else:
        result = asyncio.run(agent_framework_agent.run(request_path))
        assert result["answer"] == "answer"
        assert result["agent_identity"] == "agent-app"
        assert result["blueprint"] == "blueprint-app"
        assert isinstance(clients[0]["credential"], agent_framework_agent.DelegatedAgentCredential)
    acquire.assert_awaited_once_with(agent_framework_agent.FOUNDRY_SCOPE)


def test_delegated_credential_caches_per_scope_and_refreshes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workiq import WORKIQ_ASK_SCOPE

    credential = agent_framework_agent.DelegatedAgentCredential("tenant", "blueprint", "agent", None, "user-token")
    now = [1000]
    monkeypatch.setattr(agent_framework_agent.time, "time", lambda: now[0])
    calls: list[str] = []

    def acquire(agent: str, user: str, scope: str) -> tuple[str, int]:
        assert (agent, user) == ("agent", "user-token")
        calls.append(scope)
        return f"token-{scope}-{len(calls)}", now[0] + 3600

    monkeypatch.setattr(credential._provider, "agent_obo_token", acquire)

    async def exercise() -> None:
        first = await credential.get_token(agent_framework_agent.FOUNDRY_SCOPE)
        assert await credential.get_token(agent_framework_agent.FOUNDRY_SCOPE) == first
        assert (await credential.get_token(WORKIQ_ASK_SCOPE)).token != first.token
        now[0] += 3301
        assert (await credential.get_token(agent_framework_agent.FOUNDRY_SCOPE)).token != first.token
        with pytest.raises(ValueError, match="app-only"):
            await credential.get_token(agent_framework_agent.OBSERVABILITY_SCOPE)
        await credential.close()

    asyncio.run(exercise())
    assert calls == [agent_framework_agent.FOUNDRY_SCOPE, WORKIQ_ASK_SCOPE, agent_framework_agent.FOUNDRY_SCOPE]


def test_delegated_credential_rejects_missing_user_and_expired_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="user assertion"):
        agent_framework_agent.DelegatedAgentCredential("tenant", "blueprint", "agent", None, "")
    credential = agent_framework_agent.DelegatedAgentCredential("tenant", "blueprint", "agent", None, "user-token")
    monkeypatch.setattr(credential._provider, "agent_obo_token", lambda *args: ("expired", 0))
    with pytest.raises(RuntimeError, match="already expired"):
        asyncio.run(credential.get_token("scope"))


def test_delegated_credential_caches_ask_and_ask_act_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workiq import WORKIQ_ACT_SCOPE, WORKIQ_ASK_SCOPE

    credential = agent_framework_agent.DelegatedAgentCredential("tenant", "blueprint", "agent", None, "user-token")
    calls: list[tuple[str, ...]] = []

    def acquire(agent: str, user: str, *scopes: str) -> tuple[str, int]:
        assert (agent, user) == ("agent", "user-token")
        calls.append(scopes)
        return f"token-{len(calls)}", int(agent_framework_agent.time.time()) + 3600

    monkeypatch.setattr(credential._provider, "agent_obo_token", acquire)

    async def exercise() -> None:
        ask = await credential.get_token(WORKIQ_ASK_SCOPE)
        act = await credential.get_token(WORKIQ_ASK_SCOPE, WORKIQ_ACT_SCOPE)
        assert ask.token != act.token
        assert await credential.get_token(WORKIQ_ASK_SCOPE) == ask
        assert await credential.get_token(WORKIQ_ASK_SCOPE, WORKIQ_ACT_SCOPE) == act

    asyncio.run(exercise())
    assert calls == [(WORKIQ_ASK_SCOPE,), (WORKIQ_ASK_SCOPE, WORKIQ_ACT_SCOPE)]
