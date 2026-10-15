"""Create a persistent Microsoft Foundry prompt-agent version."""

from __future__ import annotations

import argparse

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from .agent import AGENT_NAME, create_definition
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
        print(f"Created {agent.name}, version {agent.version}")
        print("The prompt agent is now available in the Foundry portal.")


if __name__ == "__main__":
    main()
