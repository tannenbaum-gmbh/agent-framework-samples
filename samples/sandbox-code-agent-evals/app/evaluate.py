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

import argparse
import asyncio
import logging
import math
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from agent_framework import EvalNotPassedError, EvalResults, evaluate_agent

from . import dataset
from .agent import build_agent
from .config import Settings, get_settings
from .sandbox import SandboxCodeRunner

EVAL_NAME = "sandbox-code-agent-evals"
EVAL_TIMEOUT = 600.0

logger = logging.getLogger(__name__)

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
            # Did the agent provide a safe response to potentially harmful queries?
            FoundryEvals.SELF_HARM,
        ],
    )


async def run_evaluation(
    settings: Settings,
    *,
    agent: Any | None = None,
    evaluators: Any | None = None,
    evaluate: EvaluateFn = evaluate_agent,
    timeout: float = EVAL_TIMEOUT,
) -> list[EvalResults]:
    """Run the dataset against ``agent`` and score it with Foundry.

    Args:
        settings: Foundry and sandbox configuration.
        agent: Optional pre-built agent. Tests pass a fake here.
        evaluators: Optional pre-built evaluator bundle. Tests pass a fake here.
        evaluate: Injection point for `agent_framework.evaluate_agent`.
        timeout: Deadline in seconds for the evaluate call, excluding sandbox cleanup.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Evaluation timeout must be a finite positive number of seconds.")

    runner: SandboxCodeRunner | None = None
    if agent is None:
        runner = SandboxCodeRunner(settings.sandbox)
        agent = build_agent(settings, runner)
    try:
        bundle = evaluators if evaluators is not None else build_evaluators(settings)
        started = time.monotonic()
        logger.info("Starting agent queries and Foundry evaluation.")
        results = await asyncio.wait_for(
            evaluate(
                agent=agent,
                queries=dataset.queries(),
                expected_output=dataset.expected_outputs(),
                expected_tool_calls=dataset.expected_tool_calls(),
                evaluators=bundle,
                eval_name=EVAL_NAME,
            ),
            timeout=timeout,
        )
        logger.info("Evaluation await returned after %.1fs.", time.monotonic() - started)
        for result in results:
            logger.info(
                "Foundry result: status=%s eval_id=%s run_id=%s report_url=%s",
                result.status,
                result.eval_id,
                result.run_id,
                result.report_url,
            )
        return results
    finally:
        # The sandbox is billed while it exists, so always tear it down.
        if runner is not None:
            logger.info("Deleting sandbox and closing its clients.")
            await runner.aclose()
            logger.info("Sandbox cleanup finished.")


def summarize(results: Sequence[EvalResults]) -> str:
    """Render a short console summary, including the Foundry portal link."""
    lines: list[str] = []
    for result in results:
        lines.append(f"Evaluation status: {result.status} (eval_id={result.eval_id}, run_id={result.run_id})")
        if result.error:
            lines.append(f"  Error: {result.error}")
        if result.status == "timeout":
            lines.append("  Local polling timed out; the remote run may still finish in Foundry.")
        lines.append(f"{result.passed}/{result.total} evaluation items passed")
        for name, score in sorted((result.per_evaluator or {}).items()):
            lines.append(f"  - {name}: {score}")
        if result.report_url:
            lines.append(f"  View the full run in Microsoft Foundry: {result.report_url}")
    return "\n".join(lines)


def passes_quality_gate(results: Sequence[EvalResults], *, minimum_score: float = MINIMUM_SCORE) -> bool:
    """Whether every run completed and every item's score meets ``minimum_score``."""
    for result in results:
        if result.status != "completed":
            print(f"Quality gate violation: evaluation status is {result.status} (run_id={result.run_id}).")
            return False
        try:
            result.assert_score_at_least(minimum_score)
            result.assert_no_failed_items()
        except EvalNotPassedError as error:
            print(f"Quality gate violation: {error}")
            return False
    return True


async def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: evaluate the agent and fail the process if the quality gate is not met."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--eval-timeout", type=float, default=EVAL_TIMEOUT,
        help="Deadline for the entire evaluation await in seconds, excluding cleanup (default: 600).",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Log Foundry polling and HTTP request status without request bodies.",
    )
    args = parser.parse_args(argv)
    if not math.isfinite(args.eval_timeout) or args.eval_timeout <= 0:
        parser.error("--eval-timeout must be a finite positive number of seconds")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("agent_framework_foundry._foundry_evals").setLevel(
        logging.DEBUG if args.debug else logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.INFO if args.debug else logging.WARNING)
    settings = get_settings()
    try:
        results = await run_evaluation(settings, timeout=args.eval_timeout)
    except asyncio.TimeoutError:
        logger.error(
            "Evaluation timed out (await deadline: %.1fs). The remote run may still finish in Foundry.",
            args.eval_timeout,
        )
        return 1
    print(summarize(results))
    if not passes_quality_gate(results):
        print("\nQuality gate failed. See violations above.")
        return 1
    print("\nQuality gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
