"""Run an architecture review using two existing Foundry prompt agents."""

from __future__ import annotations

import argparse
import asyncio

import httpx
from agent_framework.a2a import A2AAgent
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential

from .agents import ArchitectAgent, AzureA2AAuth, DocumentationAgent
from .config import get_settings
from .workflow import ArchitectureWorkflow

DEFAULT_QUESTION = (
    "Design a tier-1 Azure application platform with private networking and "
    "regional recovery. Apply our internal requirements and validate the "
    "Azure service capabilities with Microsoft documentation."
)


async def _run(question: str, show_steps: bool) -> None:
    settings = get_settings()
    async with (
        DefaultAzureCredential() as credential,
        AIProjectClient(
            endpoint=settings.project_endpoint, credential=credential
        ) as project,
        project.get_openai_client() as responses,
        httpx.AsyncClient(timeout=120, auth=AzureA2AAuth(credential)) as http,
        A2AAgent(
            name=settings.mslearn_agent_name,
            url=settings.mslearn_a2a_endpoint,
            http_client=http,
        ) as remote_documentation,
    ):
        architect = ArchitectAgent(responses, settings.architect_agent_name)
        documentation = DocumentationAgent(remote_documentation)
        result = await ArchitectureWorkflow(
            architect.respond, documentation.respond
        ).run(question)
    if show_steps:
        print(f"## Architect draft\n\n{result.draft}\n")
        print(f"## Microsoft Learn A2A review\n\n{result.documentation_review}\n")
    print(f"## Final architecture recommendation\n\n{result.final_recommendation}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument(
        "--show-steps", action="store_true", help="Print the draft and A2A review too."
    )
    args = parser.parse_args()
    asyncio.run(_run(args.question, args.show_steps))


if __name__ == "__main__":
    main()
