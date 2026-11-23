"""Configure tools, upload Python source, and route a Foundry hosted agent."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointConfig,
    AgentEndpointProtocol,
    CodeConfiguration,
    CodeDependencyResolution,
    FixedRatioVersionSelectionRule,
    HostedAgentDefinition,
    MCPToolboxTool,
    ProtocolConfiguration,
    ProtocolVersionRecord,
    ResponsesProtocolConfiguration,
    VersionSelector,
)
from azure.core.credentials import TokenCredential
from azure.core.pipeline import Pipeline
from azure.core.pipeline.policies import BearerTokenCredentialPolicy
from azure.core.pipeline.transport import HttpRequest, RequestsTransport
from azure.identity import DefaultAzureCredential

from .agent import AGENT_NAME, WORKIQ_MCP_URL
from .config import get_settings


def get_connection_auth_type(connection_id: str, credential: TokenCredential) -> str:
    # Read ARM metadata, not the connection's credentials or listSecrets.
    parts = connection_id.split("/")
    if len(parts) != 13 or parts[0] or [part.lower() for part in parts[1::2]] != [
        "subscriptions", "resourcegroups", "providers", "accounts", "projects", "connections",
    ] or parts[6].lower() != "microsoft.cognitiveservices" or any(
        not part or part in (".", "..") for part in parts[1:]
    ):
        raise ValueError("Expected an ARM project connection resource ID for Work IQ.")
    url = f"https://management.azure.com{quote(connection_id, safe='/')}?api-version=2025-06-01"
    with Pipeline(
        transport=RequestsTransport(),
        policies=[BearerTokenCredentialPolicy(credential, "https://management.azure.com/.default")],
    ) as pipeline:
        response = pipeline.run(HttpRequest("GET", url)).http_response
        if response.status_code != 200:
            raise RuntimeError(f"Unable to verify Work IQ connection auth type (ARM HTTP {response.status_code}).")
        return json.loads(response.text())["properties"]["authType"]


def create_code_zip(source_dir: Path, zip_path: Path) -> None:
    # Only runtime code is uploaded; never include .env, tokens, or dev artifacts.
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
        for name in (
            "main.py", "requirements.txt", "app/__init__.py",
            "app/agent.py", "app/config.py", "app/host.py",
        ):
            archive.write(source_dir / name, name)


def wait_until_active(
    project: AIProjectClient, agent_name: str, version: str, *, timeout: float = 600,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = project.agents.get_version(agent_name=agent_name, agent_version=version)
        status = info["status"]
        print(f"Version {version}: {status}", flush=True)
        if status == "active":
            return
        if status in {"failed", "deleting", "deleted"}:
            raise RuntimeError(f"Hosted agent provisioning stopped: {info}")
        time.sleep(5)
    raise TimeoutError(f"Version {version} did not become active within {timeout} seconds.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=AGENT_NAME)
    args = parser.parse_args()
    settings = get_settings()
    if not settings.workiq_connection_name:
        parser.error("Set WORKIQ_CONNECTION_NAME; Work IQ is required for Microsoft 365 access.")

    with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=settings.project_endpoint, credential=credential
    ) as project, tempfile.TemporaryDirectory(prefix="mea-hosted-") as temp_dir:
        connection = project.connections.get(settings.workiq_connection_name)
        if connection.target.rstrip("/") != WORKIQ_MCP_URL:
            raise RuntimeError(
                f"Work IQ connection must target {WORKIQ_MCP_URL}; "
                "do not reuse a preview A2A or unrelated connection."
            )
        if not connection.id or not connection.id.strip():
            raise ValueError("Work IQ requires a connection ID.")
        auth_type = get_connection_auth_type(connection.id, credential)
        if auth_type not in {"OAuth2", "AgentOnBehalfOfUser"}:
            raise RuntimeError("Work IQ requires an OAuth2 or AgentOnBehalfOfUser connection; shared auth is forbidden.")
        toolbox = project.toolboxes.create_version(
            name=f"{args.name}-workiq",
            description="Work IQ MCP with per-user delegation for the hosted agent.",
            tools=[MCPToolboxTool(
                server_label="WorkIQ",
                server_url=WORKIQ_MCP_URL,
                project_connection_id=connection.id,
                require_approval="never",
            )],
        )
        print(f"Work IQ toolbox {toolbox.name}, version {toolbox.version}, auth {auth_type}", flush=True)
        zip_path = Path(temp_dir) / "agent.zip"
        create_code_zip(Path(__file__).resolve().parent.parent, zip_path)
        with zip_path.open("rb") as code:
            agent = project.agents.create_version_from_code(
                agent_name=args.name,
                definition=HostedAgentDefinition(
                    protocol_versions=[
                        ProtocolVersionRecord(protocol=AgentEndpointProtocol.RESPONSES, version="2.0.0")
                    ],
                    cpu="1",
                    memory="2Gi",
                    code_configuration=CodeConfiguration(
                        runtime="python_3_14",
                        entry_point=["python", "main.py"],
                        dependency_resolution=CodeDependencyResolution.REMOTE_BUILD,
                    ),
                    environment_variables={
                        "FOUNDRY_PROJECT_ENDPOINT": settings.project_endpoint,
                        "AZURE_AI_MODEL_DEPLOYMENT_NAME": settings.model_deployment,
                        "WORKIQ_TOOLBOX_NAME": toolbox.name,
                        "WORKIQ_TOOLBOX_VERSION": toolbox.version,
                    },
                ),
                code=code,
            )
        print(f"Created {agent.name}, version {agent.version}", flush=True)
        wait_until_active(project, args.name, agent.version)
        endpoint = project.agents.get(agent_name=args.name).agent_endpoint
        protocols = endpoint.protocol_configuration if endpoint else None
        protocols = protocols or ProtocolConfiguration()
        protocols.responses = ResponsesProtocolConfiguration()
        project.agents.update_details(
            agent_name=args.name,
            agent_endpoint=AgentEndpointConfig(
                version_selector=VersionSelector(version_selection_rules=[
                    FixedRatioVersionSelectionRule(agent_version=agent.version, traffic_percentage=100)
                ]),
                protocol_configuration=protocols,
                authorization_schemes=endpoint.authorization_schemes if endpoint else None,
            ),
        )
        print(f"Agent endpoint routed to version {agent.version}", flush=True)
        print("Microsoft Learn: full MCP catalog; all tool calls auto-approved.")
        print("Work IQ: full catalog, including SharePoint; all tool calls auto-approved.")


if __name__ == "__main__":
    main()
