import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from agent_framework_purview._client import PurviewClient
from azure.core.credentials import AccessToken


@pytest.fixture
def purview_credential():
    # Synthetic app-only JWT payload; no live credential or signature is used.
    payload = base64.urlsafe_b64encode(json.dumps({
        "idtyp": "app",
        "tid": "11111111-1111-1111-1111-111111111111",
        "appid": "22222222-2222-2222-2222-222222222222",
    }).encode()).decode().rstrip("=")
    return SimpleNamespace(
        get_token=MagicMock(return_value=AccessToken(f"test.{payload}.test", 9999999999)),
    )


@pytest.fixture
def purview_graph(monkeypatch):
    state = {"requests": [], "block_activity": None, "status": 200, "timeout": False}

    def transport(request):
        state["requests"].append(request)
        if state["timeout"]:
            raise httpx.ReadTimeout("Test timeout", request=request)
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": {"message": "Test failure"}})
        payload = json.loads(request.content)
        if request.url.path.endswith("/processContent"):
            activity = payload["contentToProcess"]["activityMetadata"]["activity"]
            actions = (
                [{"action": "restrictAccess", "restrictionAction": "block"}]
                if activity == state["block_activity"] else []
            )
            return httpx.Response(200, json={"id": "test-evaluation", "policyActions": actions})
        if request.url.path.endswith("/compute"):
            return httpx.Response(200, json={"value": []})
        return httpx.Response(200, json={})

    original_init = PurviewClient.__init__

    def init(client, *args, **kwargs):
        original_init(client, *args, **kwargs)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))

    monkeypatch.setattr(PurviewClient, "__init__", init)
    return state
