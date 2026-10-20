"""Manage the keyless RemoteTool connection through the ARM preview API."""

from __future__ import annotations

from azure.core import PipelineClient
from azure.core.credentials import TokenCredential
from azure.core.exceptions import HttpResponseError
from azure.core.pipeline.policies import BearerTokenCredentialPolicy, RetryPolicy
from azure.core.rest import HttpRequest

from .config import CONNECTION_API_VERSION, Settings

ARM_ENDPOINT = "https://management.azure.com"


def manage_connection(
    credential: TokenCredential, settings: Settings, *, delete: bool = False
) -> None:
    resource_id = settings.require_project_resource_id()
    url = (
        f"{ARM_ENDPOINT}{resource_id}/connections/{settings.connection_name}"
        f"?api-version={CONNECTION_API_VERSION}"
    )
    body = None if delete else {
        "properties": {
            "authType": "ProjectManagedIdentity",
            "category": "RemoteTool",
            "target": settings.mcp_endpoint,
            "isSharedToAll": True,
            "audience": "https://search.azure.com/",
            "metadata": {"ApiType": "Azure"},
        }
    }
    with PipelineClient(
        base_url=ARM_ENDPOINT,
        policies=[
            RetryPolicy(),
            BearerTokenCredentialPolicy(credential, f"{ARM_ENDPOINT}/.default"),
        ],
    ) as client:
        with client.send_request(
            HttpRequest("DELETE" if delete else "PUT", url, json=body)
        ) as response:
            if delete and response.status_code == 404:
                print(f"Connection {settings.connection_name} already absent.")
                return
            if response.status_code not in (200, 201, 204):
                raise HttpResponseError(
                    message=f"MCP connection operation failed: {response.text()}",
                    response=response,
                )
    print(f"{'Deleted' if delete else 'Configured'} connection {settings.connection_name}.")
