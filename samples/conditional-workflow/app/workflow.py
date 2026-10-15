"""A multi-step content workflow with a conditional revision branch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Never

from agent_framework import (
    Agent,
    AgentExecutorResponse,
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)

from .agents import build_agents
from .config import Settings

AgentFactory = Callable[[], tuple[Agent, Agent, Agent, Agent]]


def _review_status(response: AgentExecutorResponse) -> str:
    return response.agent_response.text.strip().partition("\n")[0].upper()


def _is_approved(response: AgentExecutorResponse) -> bool:
    return _review_status(response) == "APPROVED"


def _needs_revision(response: AgentExecutorResponse) -> bool:
    return not _is_approved(response)


@dataclass(frozen=True)
class FinalContent:
    text: str
    revised: bool


class Finalizer(Executor):
    """Normalizes the output from either the reviewer or reviser branch."""

    @handler
    async def finalize(
        self,
        response: AgentExecutorResponse,
        ctx: WorkflowContext[Never, FinalContent],
    ) -> None:
        text = response.agent_response.text.strip()
        if _is_approved(response):
            _, separator, approved_text = text.partition("\n")
            text = approved_text.strip() if separator else text
        await ctx.yield_output(FinalContent(text=text, revised=response.executor_id == "reviser"))


@dataclass(frozen=True)
class WorkflowRunResult:
    prompt: str
    final_content: str
    revised: bool


class ContentWorkflow:
    def __init__(self, agent_factory: AgentFactory) -> None:
        self._agent_factory = agent_factory

    @classmethod
    def from_settings(cls, settings: Settings) -> "ContentWorkflow":
        return cls(lambda: build_agents(settings))

    async def run(self, prompt: str) -> WorkflowRunResult:
        planner, writer, reviewer, reviser = self._agent_factory()
        finalizer = Finalizer(id="finalizer")
        workflow = (
            WorkflowBuilder(
                start_executor=planner,
                output_from=[finalizer],
                max_iterations=10,
                name="conditional-content-workflow",
            )
            .add_edge(planner, writer)
            .add_edge(writer, reviewer)
            .add_edge(reviewer, finalizer, condition=_is_approved)
            .add_edge(reviewer, reviser, condition=_needs_revision)
            .add_edge(reviser, finalizer)
            .build()
        )

        result = await workflow.run(prompt)
        outputs = result.get_outputs()
        if len(outputs) != 1 or not isinstance(outputs[0], FinalContent):
            raise RuntimeError(f"Expected one final content output, got {outputs!r}")

        final_content = outputs[0]
        return WorkflowRunResult(
            prompt=prompt,
            final_content=final_content.text,
            revised=final_content.revised,
        )