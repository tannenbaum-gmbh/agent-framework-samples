"""Delete an architect agent version and its optional vector store."""

from __future__ import annotations

import argparse

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from .agent import AGENT_NAME
from .config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=AGENT_NAME)
    parser.add_argument("--version", required=True, help="Version printed by app.create.")
    parser.add_argument(
        "--vector-store",
        help="Vector store ID printed by app.create. Omit to preserve it.",
    )
    args = parser.parse_args()
    settings = get_settings()

    with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=settings.project_endpoint, credential=credential
    ) as project, project.get_openai_client() as openai:
        project.agents.delete_version(
            agent_name=args.name,
            agent_version=args.version,
        )
        print(f"Deleted {args.name}, version {args.version}")

        if args.vector_store:
            openai.vector_stores.delete(args.vector_store)
            print(f"Deleted vector store {args.vector_store}")


if __name__ == "__main__":
    main()
