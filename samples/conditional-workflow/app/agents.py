"""Creates the agents used by the conditional content workflow."""

from __future__ import annotations

from agent_framework import Agent
from azure.identity import DefaultAzureCredential

from .config import Settings

PLANNER_INSTRUCTIONS = (
    "Create a concise outline for the requested content. Include the audience, key points, "
    "and any constraints. Return only the outline."
)
WRITER_INSTRUCTIONS = (
    "Write the requested content using the user's request and the preceding outline. "
    "Return only the complete draft."
)
REVIEWER_INSTRUCTIONS = (
    "Review the draft for correctness, clarity, and whether it fulfills the original request. "
    "If it is ready, reply with APPROVED on the first line followed by the complete draft. "
    "Otherwise, reply with REVISE on the first line followed by specific revision instructions."
)
REVISER_INSTRUCTIONS = (
    "Revise the draft using the review feedback in the conversation. Return only the complete, "
    "revised content without commentary or status labels."
)


def build_agents(settings: Settings) -> tuple[Agent, Agent, Agent, Agent]:
    """Create planner, writer, reviewer, and reviser agents."""
    from agent_framework.foundry import FoundryChatClient

    def create_agent(name: str, instructions: str) -> Agent:
        return Agent(
            client=FoundryChatClient(
                project_endpoint=settings.project_endpoint,
                model=settings.model_deployment,
                credential=DefaultAzureCredential(),
            ),
            name=name,
            instructions=instructions,
        )

    return (
        create_agent("planner", PLANNER_INSTRUCTIONS),
        create_agent("writer", WRITER_INSTRUCTIONS),
        create_agent("reviewer", REVIEWER_INSTRUCTIONS),
        create_agent("reviser", REVISER_INSTRUCTIONS),
    )