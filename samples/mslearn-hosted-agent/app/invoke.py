"""Invoke the deployed agent through its Foundry Responses endpoint."""

from __future__ import annotations

import argparse

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from .agent import DEFAULT_QUESTION
from .config import get_settings
from .deploy import AGENT_NAME


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--name", default=AGENT_NAME)
    args = parser.parse_args()
    settings = get_settings()
    with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=settings.project_endpoint, credential=credential
    ) as project:
        with project.get_openai_client(agent_name=args.name) as client:
            response = client.responses.create(input=args.question)
            if response.status != "completed":
                raise RuntimeError(
                    f"Hosted agent response {response.status}: {response.error or response.incomplete_details}"
                )
            print(response.output_text)


if __name__ == "__main__":
    main()
