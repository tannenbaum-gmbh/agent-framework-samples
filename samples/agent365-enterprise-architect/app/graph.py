# Copyright (c) Microsoft. All rights reserved.
"""Microsoft Graph client for Entra Agent ID blueprints and agent identities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
TOKEN_EXCHANGE_AUDIENCE = "api://AzureADTokenExchange"

# Least-privileged delegated scopes for each management operation.
SCOPES = {
    "me": ["User.Read"],
    "blueprint.create": ["AgentIdentityBlueprint.Create", "AgentIdentityBlueprintPrincipal.Create", "User.Read"],
    "blueprint.read": ["AgentIdentityBlueprint.Read.All"],
    "blueprint.credentials": ["AgentIdentityBlueprint.Read.All", "AgentIdentityBlueprint.AddRemoveCreds.All"],
    "blueprint.delete": ["AgentIdentityBlueprint.Read.All", "AgentIdentityBlueprint.DeleteRestore.All"],
    "identity.create": ["AgentIdentity.Create.All", "User.Read"],
    "identity.read": ["AgentIdentity.Read.All"],
    "identity.enable": ["AgentIdentity.EnableDisable.All"],
    "identity.delete": ["AgentIdentity.DeleteRestore.All"],
}

# Returns a bearer token for the given delegated Graph scopes. App-only providers
# (the blueprint itself) ignore the scopes and request ``.default``.
GraphTokenProvider = Callable[[list[str]], str]


class GraphError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Microsoft Graph returned {status_code}: {message}")
        self.status_code = status_code


def _user_bind(user_id: str) -> str:
    return f"{GRAPH_ROOT}/users/{user_id}"


class AgentIdentityGraphClient:
    """Create, inspect, and manage agent identity blueprints and agent identities."""

    def __init__(self, token_provider: GraphTokenProvider, *, http_client: httpx.Client | None = None) -> None:
        self._token_provider = token_provider
        self._http = http_client or httpx.Client(base_url=GRAPH_ROOT, timeout=30)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "AgentIdentityGraphClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, scopes: str, **kwargs: Any) -> Any:
        headers = {
            "Authorization": f"Bearer {self._token_provider(SCOPES[scopes])}",
            # Required whenever a request body carries @odata.type or Agent ID casts.
            "OData-Version": "4.0",
            **kwargs.pop("headers", {}),
        }
        response = self._http.request(method, path, headers=headers, **kwargs)
        if response.is_error:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.text
            raise GraphError(response.status_code, message)
        return response.json() if response.content else None

    def _list(self, path: str, scopes: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = self._request("GET", path, scopes, params=params, headers={"ConsistencyLevel": "eventual"})
        while True:
            items.extend(page.get("value", []))
            next_link = page.get("@odata.nextLink")
            if not next_link:
                return items
            page = self._request("GET", next_link, scopes, headers={"ConsistencyLevel": "eventual"})

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/me", "me", params={"$select": "id,displayName,userPrincipalName"})

    # Agent identity blueprints -------------------------------------------------

    def create_blueprint(self, display_name: str, sponsor_id: str, owner_id: str | None = None) -> dict[str, Any]:
        """Create the blueprint application and its blueprint principal in the tenant."""
        body: dict[str, Any] = {
            "@odata.type": "Microsoft.Graph.AgentIdentityBlueprint",
            "displayName": display_name,
            "sponsors@odata.bind": [_user_bind(sponsor_id)],
        }
        if owner_id:
            body["owners@odata.bind"] = [_user_bind(owner_id)]
        blueprint = self._request(
            "POST", "/applications/microsoft.graph.agentIdentityBlueprint", "blueprint.create", json=body
        )
        principal = self._request(
            "POST",
            "/servicePrincipals/microsoft.graph.agentIdentityBlueprintPrincipal",
            "blueprint.create",
            json={"appId": blueprint["appId"]},
        )
        return {"blueprint": blueprint, "principal": principal}

    def get_blueprint(self, blueprint_app_id: str) -> dict[str, Any]:
        matches = self._request(
            "GET",
            "/applications/microsoft.graph.agentIdentityBlueprint",
            "blueprint.read",
            params={"$filter": f"appId eq '{blueprint_app_id}'", "$select": "id,appId,displayName,createdDateTime"},
        ).get("value", [])
        if not matches:
            raise GraphError(404, f"No agent identity blueprint with appId {blueprint_app_id}")
        return matches[0]

    def add_blueprint_secret(self, blueprint_app_id: str, display_name: str, days: int) -> dict[str, Any]:
        """Add a client secret to the blueprint. Use for local development only."""
        object_id = self.get_blueprint(blueprint_app_id)["id"]
        end = datetime.now(timezone.utc) + timedelta(days=days)
        return self._request(
            "POST",
            f"/applications/{object_id}/microsoft.graph.agentIdentityBlueprint/addPassword",
            "blueprint.credentials",
            json={"passwordCredential": {"displayName": display_name, "endDateTime": end.isoformat()}},
        )

    def add_blueprint_managed_identity(
        self, blueprint_app_id: str, tenant_id: str, managed_identity_principal_id: str, name: str
    ) -> dict[str, Any]:
        """Trust a managed identity as the blueprint credential (recommended for production)."""
        object_id = self.get_blueprint(blueprint_app_id)["id"]
        return self._request(
            "POST",
            f"/applications/{object_id}/federatedIdentityCredentials",
            "blueprint.credentials",
            json={
                "name": name,
                "issuer": f"https://login.microsoftonline.com/{tenant_id}/v2.0",
                "subject": managed_identity_principal_id,
                "audiences": [TOKEN_EXCHANGE_AUDIENCE],
            },
        )

    def delete_blueprint(self, blueprint_app_id: str) -> None:
        """Delete the blueprint. Entra also removes all child agent identities."""
        object_id = self.get_blueprint(blueprint_app_id)["id"]
        self._request("DELETE", f"/applications/{object_id}/microsoft.graph.agentIdentityBlueprint", "blueprint.delete")

    # Agent identities ----------------------------------------------------------

    def create_agent_identity(self, blueprint_app_id: str, display_name: str, sponsor_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/servicePrincipals/microsoft.graph.agentIdentity",
            "identity.create",
            json={
                "displayName": display_name,
                "agentIdentityBlueprintId": blueprint_app_id,
                "sponsors@odata.bind": [_user_bind(sponsor_id)],
            },
        )

    def list_agent_identities(self, blueprint_app_id: str | None = None) -> list[dict[str, Any]]:
        params = {"$select": "id,appId,displayName,accountEnabled,agentIdentityBlueprintId,createdDateTime"}
        if blueprint_app_id:
            params["$filter"] = f"agentIdentityBlueprintId eq '{blueprint_app_id}'"
            params["$count"] = "true"
        return self._list("/servicePrincipals/microsoft.graph.agentIdentity", "identity.read", params)

    def get_agent_identity(self, agent_identity_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/servicePrincipals/{agent_identity_id}/microsoft.graph.agentIdentity", "identity.read"
        )

    def set_agent_identity_enabled(self, agent_identity_id: str, enabled: bool) -> None:
        """Disabling blocks new token issuance for the agent identity without deleting it."""
        self._request(
            "PATCH",
            f"/servicePrincipals/{agent_identity_id}/microsoft.graph.agentIdentity",
            "identity.enable",
            json={"accountEnabled": enabled},
        )

    def delete_agent_identity(self, agent_identity_id: str) -> None:
        self._request(
            "DELETE", f"/servicePrincipals/{agent_identity_id}/microsoft.graph.agentIdentity", "identity.delete"
        )
