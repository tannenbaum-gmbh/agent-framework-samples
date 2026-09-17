# Copyright (c) Microsoft. All rights reserved.
"""Evaluates the sandboxed code agent with Microsoft Foundry's managed evaluators.

``evaluate_agent`` runs every query in :mod:`app.dataset` against the agent - each run
really does execute model-written Python inside a Container Apps sandbox - and then sends
the resulting conversations to Foundry, which scores them with its built-in evaluators and
publishes the run to the Foundry portal.

Run it with ``python -m app.evaluate`` after setting the environment variables in
``.env.example``.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from agent_framework import EvalNotPassedError, EvalResults, evaluate_agent

from . import dataset
from .agent import build_agent
from .config import Settings, get_settings
from .sandbox import SandboxCodeRunner

EVAL_NAME = "sandbox-code-agent-evals"

MINIMUM_SCORE = 3.0
"""Quality gate. Foundry's built-in evaluators score on a 1-5 Likert scale."""

EvaluateFn = Callable[..., Awaitable[list[EvalResults]]]


def build_evaluators(settings: Settings, *, client: Any | None = None) -> Any:
    """Create the Foundry evaluator bundle used to score the agent.

    ``FoundryEvals`` submits the recorded conversations to the Foundry evaluation service,
    so the scores and the full trace show up in the project's *Evaluations* tab.
    """
    # Imported lazily so the Foundry evaluation SDK is only required when actually
    # evaluating, keeping unit tests lightweight and offline.
    from agent_framework.foundry import FoundryEvals

    if client is None:
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential

        client = FoundryChatClient(
            project_endpoint=settings.foundry.project_endpoint,
            model=settings.foundry.model_deployment,
            credential=DefaultAzureCredential(),
        )

    return FoundryEvals(
        client=client,
        evaluators=[
            # Did the agent do what the user asked, end to end?
            FoundryEvals.TASK_ADHERENCE,
            # Did it call `run_python` with sensible code instead of guessing?
            FoundryEvals.TOOL_CALL_ACCURACY,
            # Did it actually use the sandbox output in its answer?
            FoundryEvals.TOOL_OUTPUT_UTILIZATION,
            # Is the final answer an on-topic response to the question?
            FoundryEvals.RELEVANCE,
        ],
    )


async def run_evaluation(
    settings: Settings,
    *,
    agent: Any | None = None,
    evaluators: Any | None = None,
    evaluate: EvaluateFn = evaluate_agent,
) -> list[EvalResults]:
    """Run the dataset against ``agent`` and score it with Foundry.

    Args:
        settings: Foundry and sandbox configuration.
        agent: Optional pre-built agent. Tests pass a fake here.
        evaluators: Optional pre-built evaluator bundle. Tests pass a fake here.
        evaluate: Injection point for `agent_framework.evaluate_agent`.
    """
    runner: SandboxCodeRunner | None = None
    if agent is None:
        runner = SandboxCodeRunner(settings.sandbox)
        agent = build_agent(settings, runner)
    try:
        return await evaluate(
            agent=agent,
            queries=dataset.queries(),
            expected_output=dataset.expected_outputs(),
            expected_tool_calls=dataset.expected_tool_calls(),
            evaluators=evaluators if evaluators is not None else build_evaluators(settings),
            eval_name=EVAL_NAME,
        )
    finally:
        # The sandbox is billed while it exists, so always tear it down.
        if runner is not None:
            await runner.aclose()


def summarize(results: Sequence[EvalResults]) -> str:
    """Render a short console summary, including the Foundry portal link."""
    lines: list[str] = []
    for result in results:
        lines.append(f"{result.passed}/{result.total} evaluation items passed")
        for name, score in sorted((result.per_evaluator or {}).items()):
            lines.append(f"  - {name}: {score}")
        if result.report_url:
            lines.append(f"  View the full run in Microsoft Foundry: {result.report_url}")
    return "\n".join(lines)


def passes_quality_gate(results: Sequence[EvalResults], *, minimum_score: float = MINIMUM_SCORE) -> bool:
    """Whether every evaluator scored at or above ``minimum_score`` on every item."""
    for result in results:
        try:
            result.assert_score_at_least(minimum_score)
            result.assert_no_failed_items()
        except EvalNotPassedError as error:
            print(f"Quality gate violation: {error}")
            return False
    return True


async def main() -> int:
    """Entry point: evaluate the agent and fail the process if the quality gate is not met."""
    settings = get_settings()
    results = await run_evaluation(settings)
    print(summarize(results))
    if not passes_quality_gate(results):
        print(f"\nQuality gate failed: expected every evaluator to score at least {MINIMUM_SCORE}.")
        return 1
    print("\nQuality gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
