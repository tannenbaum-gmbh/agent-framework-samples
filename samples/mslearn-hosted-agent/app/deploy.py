"""Register a prebuilt ACR image as a Foundry hosted agent version."""

from __future__ import annotations

import argparse
import time

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    AgentEndpointProtocol,
    ContainerConfiguration,
    HostedAgentDefinition,
    ProtocolVersionRecord,
)
from azure.identity import DefaultAzureCredential

from .config import get_settings

AGENT_NAME = "mslearn-agent"


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
    parser.add_argument("--image", required=True, help="Fully qualified ACR image with a unique tag.")
    parser.add_argument("--name", default=AGENT_NAME)
    args = parser.parse_args()
    settings = get_settings()
    with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=settings.project_endpoint, credential=credential
    ) as project:
        agent = project.agents.create_version(
            agent_name=args.name,
            definition=HostedAgentDefinition(
                protocol_versions=[
                    ProtocolVersionRecord(protocol=AgentEndpointProtocol.RESPONSES, version="2.0.0")
                ],
                cpu="1",
                memory="2Gi",
                container_configuration=ContainerConfiguration(image=args.image),
                environment_variables={
                    #"FOUNDRY_PROJECT_ENDPOINT": settings.project_endpoint,
                    "AZURE_AI_MODEL_DEPLOYMENT_NAME": settings.model_deployment,
                },
            ),
        )
        print(f"Created {agent.name}, version {agent.version}", flush=True)
        wait_until_active(project, args.name, agent.version)


if __name__ == "__main__":
    main()
