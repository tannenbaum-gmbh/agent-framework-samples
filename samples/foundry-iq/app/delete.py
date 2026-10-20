"""Delete one agent version and optionally this sample's shared knowledge objects."""

from __future__ import annotations

import argparse

from azure.ai.projects import AIProjectClient
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient

from .config import SEARCH_API_VERSION, get_settings
from .connection import manage_connection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Version printed by app.create.")
    parser.add_argument(
        "--delete-knowledge",
        action="store_true",
        help="Also delete the connection, knowledge base, and sources shared by all versions.",
    )
    args = parser.parse_args()
    settings = get_settings()
    if args.delete_knowledge:
        settings.require_project_resource_id()
    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(settings.project_endpoint, credential=credential) as project,
    ):
        try:
            project.agents.delete_version(
                agent_name=settings.agent_name, agent_version=args.version
            )
        except ResourceNotFoundError:
            print(f"Agent {settings.agent_name}, version {args.version} already absent.")
        else:
            print(f"Deleted {settings.agent_name}, version {args.version}.")
        if args.delete_knowledge:
            manage_connection(credential, settings, delete=True)
            with SearchIndexClient(
                settings.search_endpoint, credential, api_version=SEARCH_API_VERSION
            ) as indexes:
                try:
                    indexes.delete_knowledge_base(settings.knowledge_base_name)
                except ResourceNotFoundError:
                    print(f"Knowledge base {settings.knowledge_base_name} already absent.")
                else:
                    print(f"Deleted knowledge base {settings.knowledge_base_name}.")
                for name in settings.source_names:
                    try:
                        indexes.delete_knowledge_source(name)
                    except ResourceNotFoundError:
                        print(f"Knowledge source {name} already absent.")
                    else:
                        print(f"Deleted knowledge source {name}.")


if __name__ == "__main__":
    main()
