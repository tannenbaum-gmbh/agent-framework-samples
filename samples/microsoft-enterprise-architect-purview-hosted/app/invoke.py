"""Invoke the hosted endpoint with Foundry-managed MCP tools."""

from __future__ import annotations

import argparse
import sys

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from .agent import AGENT_NAME, DEFAULT_QUESTION
from .config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--name", default=AGENT_NAME)
    parser.add_argument("--debug", action="store_true", help="Print full responses; may contain private data.")
    args = parser.parse_args()
    settings = get_settings()
    with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=settings.project_endpoint, credential=credential
    ) as project, project.get_openai_client(agent_name=args.name) as client:
        response = client.responses.create(input=args.question, store=True)
        if args.debug:
            print(response.model_dump_json(indent=2), file=sys.stderr)
        if response.status != "completed":
            raise RuntimeError(
                f"Hosted agent response {response.status}: {response.error or response.incomplete_details}"
            )
        for item in response.output:
            if item.type == "mcp_call" and item.error:
                raise RuntimeError(f"MCP tool error: {item.error}")
            if item.type == "oauth_consent_request":
                raise RuntimeError(
                    "MCP OAuth consent is required. Complete consent in the Foundry playground "
                    "as the intended user, then retry."
                )
            if item.type == "mcp_approval_request":
                raise RuntimeError(
                    "Unexpected MCP approval request. Deploy a new version with app.deploy; "
                    'MCP tools must use require_approval="never".'
                )
        print(response.output_text)


if __name__ == "__main__":
    main()
