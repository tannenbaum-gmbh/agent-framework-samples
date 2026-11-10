# Copyright (c) Microsoft. All rights reserved.
"""Acquire tokens for an Entra agent identity through its agent identity blueprint."""

from __future__ import annotations

import time
from typing import Any, Callable

TOKEN_EXCHANGE_SCOPE = "api://AzureADTokenExchange/.default"
GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"
OBSERVABILITY_SCOPE = "api://9b975845-388f-4429-889e-eab1ef63949c/.default"

AppFactory = Callable[..., Any]
AssertionProvider = Callable[[], str]


def _default_app_factory(client_id: str, **kwargs: Any) -> Any:
    import msal

    return msal.ConfidentialClientApplication(client_id, **kwargs)


def managed_identity_assertion(client_id: str | None) -> AssertionProvider:
    """Use a managed identity token as the blueprint's federated client assertion."""

    def assertion() -> str:
        from azure.identity import ManagedIdentityCredential

        credential = ManagedIdentityCredential(client_id=client_id) if client_id else ManagedIdentityCredential()
        try:
            return credential.get_token(TOKEN_EXCHANGE_SCOPE).token
        finally:
            credential.close()

    return assertion


def _require_token(result: dict[str, Any], step: str) -> str:
    if "access_token" not in result:
        detail = result.get("error_description") or result.get("error") or "unknown error"
        raise RuntimeError(f"{step} failed: {detail}")
    return result["access_token"]


class AgentIdentityTokenProvider:
    """Implements the Entra Agent ID autonomous-agent token flow.

    1. The blueprint authenticates with its own credential and requests a token exchange
       token for ``api://AzureADTokenExchange`` with ``fmi_path`` set to the agent identity.
    2. That token is used as the client assertion of the agent identity to request a token
       for the target resource. The resulting token's subject is the agent identity.
    """

    def __init__(
        self,
        tenant_id: str,
        blueprint_app_id: str,
        *,
        client_secret: str | None = None,
        assertion_provider: AssertionProvider | None = None,
        app_factory: AppFactory = _default_app_factory,
    ) -> None:
        if not client_secret and assertion_provider is None:
            raise ValueError("Provide a blueprint client secret or a managed identity assertion provider")
        self._authority = f"https://login.microsoftonline.com/{tenant_id}"
        self._blueprint_app_id = blueprint_app_id
        self._client_secret = client_secret
        self._assertion_provider = assertion_provider
        self._app_factory = app_factory
        self._blueprint_app: Any = None
        self._agent_apps: dict[str, Any] = {}

    @classmethod
    def from_settings(cls, settings: Any, **kwargs: Any) -> "AgentIdentityTokenProvider":
        if not settings.blueprint_app_id:
            raise RuntimeError("AGENT_BLUEPRINT_APP_ID environment variable is required")
        assertion = None
        if not settings.blueprint_client_secret:
            assertion = managed_identity_assertion(settings.managed_identity_client_id)
        return cls(
            settings.tenant_id,
            settings.blueprint_app_id,
            client_secret=settings.blueprint_client_secret,
            assertion_provider=assertion,
            **kwargs,
        )

    def _blueprint(self) -> Any:
        if self._blueprint_app is None:
            credential: Any = self._client_secret or {"client_assertion": self._assertion_provider}
            self._blueprint_app = self._app_factory(
                self._blueprint_app_id, authority=self._authority, client_credential=credential
            )
        return self._blueprint_app

    def blueprint_token(self, scope: str = GRAPH_DEFAULT_SCOPE) -> str:
        """App-only token for the blueprint itself, used to manage its agent identities."""
        return _require_token(self._blueprint().acquire_token_for_client([scope]), "Blueprint token request")

    def _exchange_token(self, agent_identity_client_id: str) -> str:
        return _require_token(
            self._blueprint().acquire_token_for_client([TOKEN_EXCHANGE_SCOPE], fmi_path=agent_identity_client_id),
            "Blueprint token exchange request",
        )

    def _agent_app(self, agent_identity_client_id: str) -> Any:
        if agent_identity_client_id not in self._agent_apps:
            self._agent_apps[agent_identity_client_id] = self._app_factory(
                agent_identity_client_id,
                authority=self._authority,
                client_credential={"client_assertion": lambda: self._exchange_token(agent_identity_client_id)},
            )
        return self._agent_apps[agent_identity_client_id]

    def agent_identity_token(self, agent_identity_client_id: str, scope: str) -> tuple[str, int]:
        """Token whose subject is the agent identity, for the given resource scope."""
        result = self._agent_app(agent_identity_client_id).acquire_token_for_client([scope])
        token = _require_token(result, "Agent identity token request")
        return token, int(time.time()) + int(result.get("expires_in", 0))
