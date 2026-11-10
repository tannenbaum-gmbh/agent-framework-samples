# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from azure.core.credentials import AccessToken
from microsoft_agents.hosting.core import AuthTypes
from msal import UserAssignedManagedIdentity

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import sandbox_agent  # noqa: E402


def make_credential() -> sandbox_agent.AgentIdentityCredential:
    return sandbox_agent.AgentIdentityCredential("tenant", "blueprint-app", "agent-app", "managed-identity-client")


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
            assert scopes == [sandbox_agent.FOUNDRY_SCOPE]
            return {"access_token": f"agent-token-{len(calls)}", "expires_in": 3600}

    monkeypatch.setattr(sandbox_agent, "ConfidentialClientApplication", FakeApp)
    monkeypatch.setattr(sandbox_agent.time, "time", lambda: 1000)

    async def acquire_twice() -> None:
        assert await credential.get_token(sandbox_agent.FOUNDRY_SCOPE) == AccessToken("agent-token-1", 4600)
        assert await credential.get_token(sandbox_agent.FOUNDRY_SCOPE) == AccessToken("agent-token-2", 4600)
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
        asyncio.run(credential.get_token(sandbox_agent.FOUNDRY_SCOPE))


def test_managed_identity_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = make_credential()
    monkeypatch.setattr(
        credential._connection, "get_agentic_application_token", AsyncMock(side_effect=RuntimeError("MI unavailable"))
    )
    with pytest.raises(RuntimeError, match="MI unavailable"):
        asyncio.run(credential.get_token(sandbox_agent.FOUNDRY_SCOPE))


def test_resource_token_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = make_credential()
    monkeypatch.setattr(credential._connection, "get_agentic_application_token", AsyncMock(return_value="assertion"))

    class FailingApp:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def acquire_token_for_client(self, scopes: list[str]) -> dict[str, Any]:
            return {"error": "unauthorized_client", "error_description": "agent disabled"}

    monkeypatch.setattr(sandbox_agent, "ConfidentialClientApplication", FailingApp)
    with pytest.raises(RuntimeError, match="Agent identity token request failed: agent disabled"):
        asyncio.run(credential.get_token(sandbox_agent.FOUNDRY_SCOPE))


def test_observability_token_reuses_federation_with_its_own_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = make_credential()
    exchange = AsyncMock(return_value="assertion")
    monkeypatch.setattr(credential._connection, "get_agentic_application_token", exchange)

    class FakeApp:
        def __init__(self, client_id: str, **kwargs: Any) -> None:
            assert client_id == "agent-app"
            assert kwargs["client_credential"] == {"client_assertion": "assertion"}

        def acquire_token_for_client(self, scopes: list[str]) -> dict[str, Any]:
            assert scopes == [sandbox_agent.OBSERVABILITY_SCOPE]
            return {"access_token": "observability-token", "expires_in": 3600}

    monkeypatch.setattr(sandbox_agent, "ConfidentialClientApplication", FakeApp)
    token = asyncio.run(credential.get_token(sandbox_agent.OBSERVABILITY_SCOPE))
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

    monkeypatch.setattr(sandbox_agent, "managed_identity_assertion", assertion_provider)

    def acquire(self: Any, agent_id: str, scope: str) -> tuple[str, int]:
        assert agent_id == "agent"
        assert scope == sandbox_agent.FOUNDRY_SCOPE
        assert self._client_secret is None
        assert self._assertion_provider() == "system-identity-assertion"
        return "system-agent-token", 4600

    monkeypatch.setattr(agent_token.AgentIdentityTokenProvider, "agent_identity_token", acquire)
    credential = sandbox_agent.AgentIdentityCredential("tenant", "blueprint", "agent", None)
    token = asyncio.run(credential.get_token(sandbox_agent.FOUNDRY_SCOPE))
    assert token == AccessToken("system-agent-token", 4600)
    assert calls == [None]


def test_credential_requires_agent_identity_configuration() -> None:
    with pytest.raises(ValueError, match="Tenant, blueprint, and agent client IDs are required"):
        sandbox_agent.AgentIdentityCredential("tenant", "", "agent", None)


def test_credential_supports_azure_async_context_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = make_credential()
    close = AsyncMock()
    monkeypatch.setattr(credential, "close", close)

    async def exercise() -> None:
        async with credential as entered:
            assert entered is credential

    asyncio.run(exercise())
    close.assert_awaited_once()


def test_obo_credential_exchanges_caller_token_but_telemetry_remains_app_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = make_credential()
    monkeypatch.setattr(credential._connection, "get_agentic_application_token", AsyncMock(return_value="fmi-token"))
    calls: list[str] = []

    class FakeApp:
        def __init__(self, client_id: str, **kwargs: Any) -> None:
            assert client_id == "agent-app"
            assert kwargs["client_credential"] == {"client_assertion": "fmi-token"}

        def acquire_token_on_behalf_of(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs == {"user_assertion": "caller-token", "scopes": [sandbox_agent.FOUNDRY_SCOPE]}
            calls.append("obo")
            return {"access_token": "delegated-token", "expires_in": 3600}

        def acquire_token_for_client(self, scopes: list[str]) -> dict[str, Any]:
            assert scopes == [sandbox_agent.OBSERVABILITY_SCOPE]
            calls.append("s2s")
            return {"access_token": "telemetry-token", "expires_in": 3600}

    monkeypatch.setattr(sandbox_agent, "ConfidentialClientApplication", FakeApp)

    async def exercise() -> None:
        assert (await credential.get_obo_token("caller-token")).token == "delegated-token"
        assert (await credential.get_token(sandbox_agent.OBSERVABILITY_SCOPE)).token == "telemetry-token"
        with pytest.raises(ValueError, match="caller access token"):
            await credential.get_obo_token(" ")

    asyncio.run(exercise())
    assert calls == ["obo", "s2s"]


def test_system_assigned_identity_supports_obo(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent_token import AgentIdentityTokenProvider

    def acquire(self: Any, agent: str, assertion: str, scope: str) -> tuple[str, int]:
        assert (agent, assertion, scope) == ("agent", "caller-token", sandbox_agent.FOUNDRY_SCOPE)
        return "delegated-token", 2_000_000_000

    monkeypatch.setattr(AgentIdentityTokenProvider, "agent_identity_obo_token", acquire)
    credential = sandbox_agent.AgentIdentityCredential("tenant", "blueprint", "agent", None)
    assert asyncio.run(credential.get_obo_token("caller-token")).token == "delegated-token"


@pytest.mark.parametrize("failure", [None, "consent required", "missing caller", "wrong tenant"])
def test_obo_runtime_removes_assertion_before_model_call_even_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None,
) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps({
        "tenant_id": "tenant", "blueprint_app_id": "blueprint-app",
        "agent_identity_client_id": "agent-app", "auth_mode": "obo",
        "user_assertion": "caller-token", "question": "hello",
    }))
    claims = {
        "azp": "agent-app", "xms_par_app_azp": "blueprint-app",
        "oid": "caller", "scp": "user_impersonation", "tid": "tenant",
    }
    if failure == "missing caller":
        del claims["oid"]
    if failure == "wrong tenant":
        claims["tid"] = "other-tenant"
    token = AccessToken(jwt.encode(claims, "test-signing-key-that-is-long-enough-for-hs256"), 2_000_000_000)
    acquire = AsyncMock(return_value=token)
    if failure == "consent required":
        acquire.side_effect = RuntimeError(failure)
    app_only = AsyncMock(side_effect=AssertionError("Must not fall back to app-only Foundry token"))
    close = AsyncMock()
    invoke = MagicMock(return_value="answer")
    monkeypatch.setattr(sandbox_agent.AgentIdentityCredential, "get_obo_token", acquire)
    monkeypatch.setattr(sandbox_agent.AgentIdentityCredential, "get_token", app_only)
    monkeypatch.setattr(sandbox_agent.AgentIdentityCredential, "close", close)
    monkeypatch.setattr(sandbox_agent, "invoke_prompt_agent", invoke)
    if failure:
        with pytest.raises(RuntimeError):
            asyncio.run(sandbox_agent.run(path))
        invoke.assert_not_called()
    else:
        assert asyncio.run(sandbox_agent.run(path))["answer"] == "answer"
        assert "user_assertion" not in invoke.call_args.args[0]
        assert invoke.call_args.args[1] == token
    acquire.assert_awaited_once_with("caller-token")
    app_only.assert_not_awaited()
    close.assert_awaited_once()
    assert not path.exists()


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
                "agent_name": "architect",
                "agent_version": "3",
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
    monkeypatch.setattr(sandbox_agent.AgentIdentityCredential, "get_token", acquire)
    invocations: list[tuple[dict[str, Any], AccessToken]] = []

    def fake_invoke(request: dict[str, Any], access_token: AccessToken) -> str:
        invocations.append((request, access_token))
        return "answer"

    monkeypatch.setattr(sandbox_agent, "invoke_prompt_agent", fake_invoke)
    if error:
        with pytest.raises(RuntimeError, match=error):
            asyncio.run(sandbox_agent.run(request_path))
        assert not invocations
    else:
        result = asyncio.run(sandbox_agent.run(request_path))
        assert result["answer"] == "answer"
        assert result["agent_identity"] == "agent-app"
        assert result["blueprint"] == "blueprint-app"
        assert invocations[0][0]["agent_name"] == "architect"
        assert invocations[0][0]["agent_version"] == "3"
        assert invocations[0][1].token == token
    acquire.assert_awaited_once_with(sandbox_agent.FOUNDRY_SCOPE)


def test_agent_identity_access_token_credential_is_scoped() -> None:
    token = AccessToken("agent-token", 1234)
    credential = sandbox_agent.AgentIdentityAccessTokenCredential(token)
    assert credential.get_token(sandbox_agent.FOUNDRY_SCOPE) == token
    with pytest.raises(ValueError, match="only supports"):
        credential.get_token("https://graph.microsoft.com/.default")


def test_prompt_agent_invocation_uses_identity_token_and_cleans_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import azure.ai.projects

    project = MagicMock()
    project.__enter__.return_value = project
    client = MagicMock()
    client.__enter__.return_value = client
    project.get_openai_client.return_value = client
    client.conversations.create.return_value.id = "conversation-id"
    client.responses.create.return_value = SimpleNamespace(
        status="completed", output_text="Architecture", output=[]
    )
    project_factory = MagicMock(return_value=project)
    monkeypatch.setattr(azure.ai.projects, "AIProjectClient", project_factory)

    token = AccessToken("agent-token", 1234)
    result = sandbox_agent.invoke_prompt_agent(
        {
            "project_endpoint": "https://example.test/project",
            "question": "Design a workload",
            "agent_name": "architect",
            "agent_version": "9",
        },
        token,
    )

    assert result == "Architecture"
    assert project.get_openai_client.call_args.kwargs == {}
    args = client.responses.create.call_args.kwargs
    assert args["conversation"] == "conversation-id"
    assert args["input"] == "Design a workload"
    assert args["tool_choice"] == "auto"
    assert args["extra_body"]["agent_reference"] == {
        "name": "architect",
        "version": "9",
        "type": "agent_reference",
    }
    assert client.conversations.delete.call_args.kwargs == {"conversation_id": "conversation-id"}
    credential = project_factory.call_args.kwargs["credential"]
    assert credential.get_token(sandbox_agent.FOUNDRY_SCOPE) == token
