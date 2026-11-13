"""Delete only this sample's agent, including all versions and endpoint routing."""

from __future__ import annotations

import argparse

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from .agent import AGENT_NAME
from .config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=AGENT_NAME)
    args = parser.parse_args()
    settings = get_settings()
    with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=settings.project_endpoint, credential=credential
    ) as project:
        project.agents.delete(agent_name=args.name)
    print(f"Deleted {args.name} and all its versions. Shared connections and documents were not deleted.")


if __name__ == "__main__":
    main()
