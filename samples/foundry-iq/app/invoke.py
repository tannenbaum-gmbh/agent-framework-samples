"""Invoke the agent, or inspect Foundry IQ references and retrieval activity."""

from __future__ import annotations

import argparse
import json

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.search.documents.knowledgebases import KnowledgeBaseRetrievalClient
from azure.search.documents.knowledgebases.models import (
    KnowledgeBaseMessage,
    KnowledgeBaseMessageTextContent,
    KnowledgeBaseRetrievalRequest,
)

from .agent import DEFAULT_QUESTION
from .config import SEARCH_API_VERSION, get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--retrieve-only", action="store_true")
    parser.add_argument("--version", help="Pin an agent version; otherwise use the latest.")
    args = parser.parse_args()
    if not args.question.strip():
        parser.error("question must not be empty.")
    settings = get_settings()

    with DefaultAzureCredential() as credential:
        if args.retrieve_only:
            with KnowledgeBaseRetrievalClient(
                settings.search_endpoint,
                credential,
                knowledge_base_name=settings.knowledge_base_name,
                api_version=SEARCH_API_VERSION,
            ) as client:
                result = client.retrieve(
                    KnowledgeBaseRetrievalRequest(
                        messages=[
                            KnowledgeBaseMessage(
                                role="user",
                                content=[KnowledgeBaseMessageTextContent(text=args.question)],
                            )
                        ],
                        include_activity=True,
                        max_runtime_in_seconds=120,
                    )
                )
                print(json.dumps(result.as_dict(), indent=2))
            return
        with AIProjectClient(settings.project_endpoint, credential=credential) as project:
            with project.get_openai_client() as client:
                reference = {"name": settings.agent_name, "type": "agent_reference"}
                if args.version:
                    reference["version"] = args.version
                response = client.responses.create(
                    input=args.question,
                    extra_body={"agent_reference": reference},
                )
                if response.status != "completed":
                    raise RuntimeError(
                        f"Agent response {response.status}: "
                        f"{response.error or response.incomplete_details}"
                    )
                if not response.output_text:
                    raise RuntimeError("Agent completed without a text answer.")
                print(response.output_text)


if __name__ == "__main__":
    main()
