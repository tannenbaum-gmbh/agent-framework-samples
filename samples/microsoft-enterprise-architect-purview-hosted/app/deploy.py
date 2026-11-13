"""Configure tools, upload Python source, and route a Foundry hosted agent."""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointConfig,
    AgentEndpointProtocol,
    CodeConfiguration,
    CodeDependencyResolution,
    FixedRatioVersionSelectionRule,
    HostedAgentDefinition,
    ProtocolConfiguration,
    ProtocolVersionRecord,
    ResponsesProtocolConfiguration,
    VersionSelector,
)
from azure.identity import DefaultAzureCredential

from .agent import AGENT_NAME, WORKIQ_MCP_URL
from .config import get_settings


def create_code_zip(source_dir: Path, zip_path: Path) -> None:
    # Only runtime code is uploaded; never include .env, tokens, or dev artifacts.
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
        for name in (
            "main.py", "requirements.txt", "app/__init__.py",
            "app/agent.py", "app/config.py", "app/host.py", "app/purview.py",
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
    ) as project, tempfile.TemporaryDirectory(prefix="mea-purview-hosted-") as temp_dir:
        connection = project.connections.get(settings.workiq_connection_name)
        if connection.target.rstrip("/") != WORKIQ_MCP_URL:
            raise RuntimeError(
                f"Work IQ connection must target {WORKIQ_MCP_URL}; "
                "do not reuse a preview A2A or unrelated connection."
            )
        if not connection.id or not connection.id.strip():
            raise ValueError("Work IQ requires a connection ID.")
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
                        "WORKIQ_CONNECTION_ID": connection.id,
                        "PURVIEW_TENANT_ID": settings.purview_tenant_id,
                        "PURVIEW_APP_ID": settings.purview_app_id,
                        "PURVIEW_USER_ID": settings.purview_user_id,
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
