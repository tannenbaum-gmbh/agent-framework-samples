"""Invoke the persistent enterprise architect prompt agent."""

from __future__ import annotations

import argparse

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from .agent import AGENT_NAME, DEFAULT_QUESTION
from .config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--name", default=AGENT_NAME)
    args = parser.parse_args()
    settings = get_settings()

    with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=settings.project_endpoint, credential=credential
    ) as project, project.get_openai_client() as client:
        response = client.responses.create(
            input=args.question,
            extra_body={
                "agent_reference": {
                    "name": args.name,
                    "type": "agent_reference",
                }
            },
        )
        if response.status != "completed":
            raise RuntimeError(
                f"Prompt agent response {response.status}: "
                f"{response.error or response.incomplete_details}"
            )
        print(response.output_text)


if __name__ == "__main__":
    main()
