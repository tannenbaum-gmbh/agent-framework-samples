# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph import AgentIdentityGraphClient, GraphError  # noqa: E402

BLUEPRINT_APP_ID = "11111111-1111-1111-1111-111111111111"
BLUEPRINT_OBJECT_ID = "22222222-2222-2222-2222-222222222222"


class Recorder:
    def __init__(self, responses: dict[tuple[str, str], list[httpx.Response]]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []
        self.scopes: list[list[str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses[(request.method, request.url.path)].pop(0)

    def client(self) -> AgentIdentityGraphClient:
        def token(scopes: list[str]) -> str:
            self.scopes.append(scopes)
            return "graph-token"

        http = httpx.Client(base_url="https://graph.microsoft.com/v1.0", transport=httpx.MockTransport(self.handler))
        return AgentIdentityGraphClient(token, http_client=http)


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


BLUEPRINT_LOOKUP = ("GET", "/v1.0/applications/microsoft.graph.agentIdentityBlueprint")


def test_create_blueprint_creates_application_and_principal() -> None:
    recorder = Recorder(
        {
            ("POST", "/v1.0/applications/microsoft.graph.agentIdentityBlueprint"): [
                httpx.Response(201, json={"id": BLUEPRINT_OBJECT_ID, "appId": BLUEPRINT_APP_ID})
            ],
            ("POST", "/v1.0/servicePrincipals/microsoft.graph.agentIdentityBlueprintPrincipal"): [
                httpx.Response(201, json={"id": "sp-id", "appId": BLUEPRINT_APP_ID})
            ],
        }
    )
    result = recorder.client().create_blueprint("Demo", sponsor_id="sponsor", owner_id="owner")

    assert result["principal"]["id"] == "sp-id"
    create, principal = recorder.requests
    assert create.headers["OData-Version"] == "4.0"
    assert create.headers["Authorization"] == "Bearer graph-token"
    assert body(create) == {
        "@odata.type": "Microsoft.Graph.AgentIdentityBlueprint",
        "displayName": "Demo",
        "sponsors@odata.bind": ["https://graph.microsoft.com/v1.0/users/sponsor"],
        "owners@odata.bind": ["https://graph.microsoft.com/v1.0/users/owner"],
    }
    assert body(principal) == {"appId": BLUEPRINT_APP_ID}
    assert "AgentIdentityBlueprint.Create" in recorder.scopes[0]


def test_blueprint_credentials_resolve_object_id() -> None:
    lookup = httpx.Response(200, json={"value": [{"id": BLUEPRINT_OBJECT_ID, "appId": BLUEPRINT_APP_ID}]})
    recorder = Recorder(
        {
            BLUEPRINT_LOOKUP: [lookup, lookup],
            ("POST", f"/v1.0/applications/{BLUEPRINT_OBJECT_ID}/microsoft.graph.agentIdentityBlueprint/addPassword"): [
                httpx.Response(200, json={"keyId": "k", "secretText": "s", "endDateTime": "2030-01-01"})
            ],
            ("POST", f"/v1.0/applications/{BLUEPRINT_OBJECT_ID}/federatedIdentityCredentials"): [
                httpx.Response(201, json={"id": "fic"})
            ],
        }
    )
    client = recorder.client()
    assert client.add_blueprint_secret(BLUEPRINT_APP_ID, "dev", days=7)["secretText"] == "s"
    client.add_blueprint_managed_identity(BLUEPRINT_APP_ID, "tenant", "mi-principal", "mi")

    assert recorder.requests[0].url.params["$filter"] == f"appId eq '{BLUEPRINT_APP_ID}'"
    assert body(recorder.requests[1])["passwordCredential"]["displayName"] == "dev"
    assert body(recorder.requests[3]) == {
        "name": "mi",
        "issuer": "https://login.microsoftonline.com/tenant/v2.0",
        "subject": "mi-principal",
        "audiences": ["api://AzureADTokenExchange"],
    }


def test_agent_identity_lifecycle() -> None:
    identity_path = "/v1.0/servicePrincipals/agent-sp/microsoft.graph.agentIdentity"
    recorder = Recorder(
        {
            ("POST", "/v1.0/servicePrincipals/microsoft.graph.agentIdentity"): [
                httpx.Response(201, json={"id": "agent-sp", "appId": "agent-app"})
            ],
            ("GET", "/v1.0/servicePrincipals/microsoft.graph.agentIdentity"): [
                httpx.Response(
                    200,
                    json={
                        "value": [{"id": "agent-sp"}],
                        "@odata.nextLink": "https://graph.microsoft.com/v1.0/servicePrincipals/"
                        "microsoft.graph.agentIdentity?$skiptoken=abc",
                    },
                ),
                httpx.Response(200, json={"value": [{"id": "agent-sp-2"}]}),
            ],
            ("PATCH", identity_path): [httpx.Response(204), httpx.Response(204)],
            ("DELETE", identity_path): [httpx.Response(204)],
        }
    )
    client = recorder.client()

    created = client.create_agent_identity(BLUEPRINT_APP_ID, "Demo agent", "sponsor")
    listed = client.list_agent_identities(BLUEPRINT_APP_ID)
    client.set_agent_identity_enabled("agent-sp", False)
    client.set_agent_identity_enabled("agent-sp", True)
    client.delete_agent_identity("agent-sp")

    assert created["appId"] == "agent-app"
    assert body(recorder.requests[0]) == {
        "displayName": "Demo agent",
        "agentIdentityBlueprintId": BLUEPRINT_APP_ID,
        "sponsors@odata.bind": ["https://graph.microsoft.com/v1.0/users/sponsor"],
    }
    assert [item["id"] for item in listed] == ["agent-sp", "agent-sp-2"]
    assert recorder.requests[1].url.params["$filter"] == f"agentIdentityBlueprintId eq '{BLUEPRINT_APP_ID}'"
    assert recorder.requests[1].headers["ConsistencyLevel"] == "eventual"
    assert body(recorder.requests[3]) == {"accountEnabled": False}
    assert body(recorder.requests[4]) == {"accountEnabled": True}
    assert recorder.scopes[-1] == ["AgentIdentity.DeleteRestore.All"]


def test_graph_errors_surface_message() -> None:
    recorder = Recorder(
        {
            ("GET", "/v1.0/servicePrincipals/missing/microsoft.graph.agentIdentity"): [
                httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound", "message": "not found"}})
            ]
        }
    )
    with pytest.raises(GraphError, match="404: not found"):
        recorder.client().get_agent_identity("missing")


def test_missing_blueprint_raises() -> None:
    recorder = Recorder({BLUEPRINT_LOOKUP: [httpx.Response(200, json={"value": []})]})
    with pytest.raises(GraphError, match="No agent identity blueprint"):
        recorder.client().delete_blueprint(BLUEPRINT_APP_ID)
