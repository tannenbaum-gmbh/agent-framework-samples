"""Create knowledge sources, a knowledge base, a keyless connection, and an agent."""

import argparse

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient

from .agent import create_definition
from .config import SEARCH_API_VERSION, get_settings
from .connection import manage_connection
from .knowledge import build_knowledge_base, build_sources, ensure_semantic_configuration


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    settings = get_settings()
    settings.require_project_resource_id()
    with (
        DefaultAzureCredential() as credential,
        SearchIndexClient(
            settings.search_endpoint, credential, api_version=SEARCH_API_VERSION
        ) as indexes,
        AIProjectClient(settings.project_endpoint, credential=credential) as project,
    ):
        ensure_semantic_configuration(indexes, settings)
        for source in build_sources(settings):
            indexes.create_or_update_knowledge_source(source)
            print(f"Configured knowledge source {source.name}.")
        indexes.create_or_update_knowledge_base(build_knowledge_base(settings))
        print(f"Configured knowledge base {settings.knowledge_base_name}.")
        manage_connection(credential, settings)
        agent = project.agents.create_version(
            agent_name=settings.agent_name,
            definition=create_definition(settings),
        )
        print(f"Created {agent.name}, version {agent.version}")


if __name__ == "__main__":
    main()
