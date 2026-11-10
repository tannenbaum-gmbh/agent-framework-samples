# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent_token import TOKEN_EXCHANGE_SCOPE, AgentIdentityTokenProvider  # noqa: E402


class FakeApp:
    def __init__(self, client_id: str, registry: list["FakeApp"], **kwargs: Any) -> None:
        self.client_id = client_id
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        registry.append(self)

    def acquire_token_for_client(self, scopes: list[str], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"scopes": scopes, **kwargs})
        credential = self.kwargs["client_credential"]
        if self.client_id != "blueprint-app":
            # Agent identity apps authenticate with the blueprint's exchange token.
            assertion = credential["client_assertion"]()
            if assertion != "exchange-token":
                return {"error": "invalid_client", "error_description": "bad assertion"}
            return {"access_token": f"agent-token:{scopes[0]}", "expires_in": 3600}
        return {"access_token": "exchange-token" if kwargs.get("fmi_path") else "blueprint-token", "expires_in": 3600}


def make_provider(**kwargs: Any) -> tuple[AgentIdentityTokenProvider, list[FakeApp]]:
    apps: list[FakeApp] = []
    provider = AgentIdentityTokenProvider(
        "tenant",
        "blueprint-app",
        app_factory=lambda client_id, **kw: FakeApp(client_id, apps, **kw),
        **kwargs,
    )
    return provider, apps


def test_agent_identity_token_uses_blueprint_exchange_with_fmi_path() -> None:
    provider, apps = make_provider(client_secret="secret")

    token, expires_on = provider.agent_identity_token("agent-app", "https://ai.azure.com/.default")

    assert token == "agent-token:https://ai.azure.com/.default"
    assert expires_on > 0
    blueprint, agent = sorted(apps, key=lambda app: app.client_id != "blueprint-app")
    assert blueprint.client_id == "blueprint-app"
    assert blueprint.kwargs["client_credential"] == "secret"
    assert blueprint.kwargs["authority"] == "https://login.microsoftonline.com/tenant"
    assert blueprint.calls == [{"scopes": [TOKEN_EXCHANGE_SCOPE], "fmi_path": "agent-app"}]
    assert agent.client_id == "agent-app"


def test_agent_apps_are_reused_per_identity() -> None:
    provider, apps = make_provider(client_secret="secret")
    provider.agent_identity_token("agent-app", "scope-a")
    provider.agent_identity_token("agent-app", "scope-b")
    assert sorted(app.client_id for app in apps) == ["agent-app", "blueprint-app"]


def test_managed_identity_assertion_is_used_without_secret() -> None:
    provider, apps = make_provider(assertion_provider=lambda: "mi-token")
    assert provider.blueprint_token() == "blueprint-token"
    assert apps[0].kwargs["client_credential"]["client_assertion"]() == "mi-token"


def test_requires_a_credential() -> None:
    with pytest.raises(ValueError):
        AgentIdentityTokenProvider("tenant", "blueprint-app")


def test_token_errors_are_reported() -> None:
    apps: list[FakeApp] = []

    class FailingApp(FakeApp):
        def acquire_token_for_client(self, scopes: list[str], **kwargs: Any) -> dict[str, Any]:
            return {"error": "unauthorized_client", "error_description": "AADSTS7000112: disabled"}

    provider = AgentIdentityTokenProvider(
        "tenant", "blueprint-app", client_secret="s", app_factory=lambda cid, **kw: FailingApp(cid, apps, **kw)
    )
    with pytest.raises(RuntimeError, match="AADSTS7000112"):
        provider.agent_identity_token("agent-app", "scope")
