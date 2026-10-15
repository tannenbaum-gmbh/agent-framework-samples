"""Create a persistent Microsoft Foundry prompt-agent version."""

from __future__ import annotations

import argparse

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from .agent import (
    AGENT_NAME,
    create_agent_card,
    create_definition,
    create_endpoint_config,
)
from .config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=AGENT_NAME)
    args = parser.parse_args()
    settings = get_settings()

    with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=settings.project_endpoint, credential=credential
    ) as project:
        agent = project.agents.create_version(
            agent_name=args.name,
            definition=create_definition(settings),
        )
        project.agents.update_details(
            agent_name=args.name,
            agent_endpoint=create_endpoint_config(),
            agent_card=create_agent_card(),
        )
        print(f"Created {agent.name}, version {agent.version}")
        print("The prompt agent is available through Responses and A2A protocols.")


if __name__ == "__main__":
    main()
