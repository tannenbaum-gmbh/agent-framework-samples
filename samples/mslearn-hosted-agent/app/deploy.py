"""Upload Python source as a persistent, routed Foundry hosted agent version."""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointProtocol,
    AgentEndpointConfig,
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

from .config import get_settings

AGENT_NAME = "mslearn-agent"


def create_code_zip(source_dir: Path, zip_path: Path) -> None:
    # An allowlist keeps environment files, credentials, and dev artifacts out of uploads.
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
        for name in (
            "main.py",
            "requirements.txt",
            "app/__init__.py",
            "app/agent.py",
            "app/config.py",
            "app/host.py",
        ):
            archive.write(source_dir / name, name)


def wait_until_active(
    project: AIProjectClient,
    agent_name: str,
    version: str,
    *,
    timeout: float = 600,
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
    with tempfile.TemporaryDirectory(prefix="mslearn-agent-") as temp_dir, DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=settings.project_endpoint, credential=credential
    ) as project:
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
                    },
                ),
                code=code,
            )
        print(f"Created {agent.name}, version {agent.version}", flush=True)
        wait_until_active(project, args.name, agent.version)
        project.agents.update_details(
            agent_name=args.name,
            agent_endpoint=AgentEndpointConfig(
                version_selector=VersionSelector(
                    version_selection_rules=[
                        FixedRatioVersionSelectionRule(agent_version=agent.version, traffic_percentage=100)
                    ]
                ),
                protocol_configuration=ProtocolConfiguration(responses=ResponsesProtocolConfiguration()),
            ),
        )
        print(f"Agent endpoint routed to version {agent.version}", flush=True)


if __name__ == "__main__":
    main()
