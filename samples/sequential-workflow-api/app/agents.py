# Copyright (c) Microsoft. All rights reserved.
"""Builds the two agents used by the sequential workflow sample.

Each call creates brand new `Agent`/chat client instances so that concurrent requests
(potentially for different threads/users) never share mutable client state.
"""

from __future__ import annotations

from agent_framework import Agent
from azure.identity import DefaultAzureCredential

from .config import Settings

RESEARCHER_INSTRUCTIONS = (
    "You are a research assistant. Given the user's question, gather the key facts, "
    "considerations, and any relevant context needed to answer it well. Reply with a "
    "short, bullet-pointed list of research notes only - do not write the final answer."
)

WRITER_INSTRUCTIONS = (
    "You are a writer. Using the research notes earlier in the conversation, write a "
    "clear, well-structured final answer for the user's original question."
)


def build_agents(settings: Settings) -> tuple[Agent, Agent]:
    """Create the `researcher` and `writer` agents backed by Microsoft Foundry.

    Returns:
        A tuple of ``(researcher, writer)`` agents to be run in that order by a
        `SequentialBuilder` workflow.
    """
    # Imported lazily so the FoundryChatClient (and its Azure SDK dependencies) is only
    # required when actually talking to Microsoft Foundry, keeping unit tests lightweight.
    from agent_framework.foundry import FoundryChatClient

    credential = DefaultAzureCredential()

    researcher = Agent(
        client=FoundryChatClient(
            project_endpoint=settings.project_endpoint,
            model=settings.model_deployment,
            credential=credential,
        ),
        name="researcher",
        instructions=RESEARCHER_INSTRUCTIONS,
    )
    writer = Agent(
        client=FoundryChatClient(
            project_endpoint=settings.project_endpoint,
            model=settings.model_deployment,
            credential=credential,
        ),
        name="writer",
        instructions=WRITER_INSTRUCTIONS,
    )
    return researcher, writer
