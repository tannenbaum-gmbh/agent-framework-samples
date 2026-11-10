"""Create a versioned Foundry prompt agent with Microsoft Learn and Work IQ."""

from __future__ import annotations

import argparse

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from .agent import AGENT_NAME, DISPLAY_NAME, WORKIQ_MCP_URL, create_definition
from .config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=AGENT_NAME)
    args = parser.parse_args()
    settings = get_settings()
    if not settings.workiq_connection_name:
        parser.error("Set WORKIQ_CONNECTION_NAME; Work IQ is required for Microsoft 365 access.")

    with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=settings.foundry_project_endpoint, credential=credential
    ) as project:
        connection = project.connections.get(settings.workiq_connection_name)
        if connection.target.rstrip("/") != WORKIQ_MCP_URL:
            raise RuntimeError(
                f"Work IQ connection must target {WORKIQ_MCP_URL}; "
                "do not reuse a preview A2A or unrelated connection."
            )
        if not connection.id or not connection.id.strip():
            raise ValueError("Work IQ requires a connection ID.")
        agent = project.agents.create_version(
            agent_name=args.name,
            definition=create_definition(settings, workiq_connection_id=connection.id),
            description=(
                f"{DISPLAY_NAME}: Agent 365 identity-authenticated caller with "
                "Microsoft Learn and the full Work IQ MCP catalog."
            ),
        )
        print(f"Created {agent.name}, version {agent.version}")
        print("Microsoft Learn: full MCP catalog; all tool calls auto-approved.")
        print("Work IQ: full catalog, including SharePoint; all tool calls auto-approved.")
        print("Work IQ uses its separate delegated OAuth connection; it does not use the agent identity.")


if __name__ == "__main__":
    main()
