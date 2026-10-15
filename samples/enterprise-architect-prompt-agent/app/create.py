"""Upload internal guidelines and create an enterprise architect prompt agent."""

from __future__ import annotations

import argparse
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from .agent import AGENT_NAME, VECTOR_STORE_NAME, create_definition
from .config import get_settings

GUIDELINES_DIR = Path(__file__).resolve().parents[1] / "internal-guidelines"


def guideline_paths() -> list[Path]:
    """Return the curated guideline documents in deterministic order."""
    paths = sorted(GUIDELINES_DIR.glob("*.md"))
    if not paths:
        raise RuntimeError(f"No Markdown guidelines found in {GUIDELINES_DIR}.")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=AGENT_NAME)
    args = parser.parse_args()
    settings = get_settings()

    with DefaultAzureCredential() as credential, AIProjectClient(
        endpoint=settings.project_endpoint, credential=credential
    ) as project, project.get_openai_client() as openai:
        connection = project.connections.get(settings.a2a_connection_name)
        vector_store = openai.vector_stores.create(name=VECTOR_STORE_NAME)

        try:
            for path in guideline_paths():
                with path.open("rb") as guideline:
                    openai.vector_stores.files.upload_and_poll(
                        vector_store_id=vector_store.id,
                        file=guideline,
                    )

            agent = project.agents.create_version(
                agent_name=args.name,
                definition=create_definition(
                    settings,
                    a2a_connection_id=connection.id,
                    vector_store_id=vector_store.id,
                ),
                description=(
                    "Enterprise architect grounded in internal cloud guidelines "
                    "and Microsoft Learn through A2A."
                ),
            )
        except Exception:
            openai.vector_stores.delete(vector_store.id)
            raise

        print(f"Created {agent.name}, version {agent.version}")
        print(f"Uploaded {len(guideline_paths())} guideline files")
        print(f"Vector store: {vector_store.id}")
        print("Keep the vector store ID to delete all sample resources later.")


if __name__ == "__main__":
    main()
