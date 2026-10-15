"""Serve the same Responses protocol locally and in Foundry."""

from __future__ import annotations

from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential

from .agent import create_agent
from .config import get_settings


def main() -> None:
    settings = get_settings()
    with DefaultAzureCredential() as credential:
        # The host enters/exits a fresh agent and its MCP connection per request.
        server = ResponsesHostServer(agent=lambda: create_agent(settings, credential))
        server.run()


if __name__ == "__main__":
    main()
