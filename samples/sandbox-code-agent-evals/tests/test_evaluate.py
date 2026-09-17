# Copyright (c) Microsoft. All rights reserved.
"""Tests for the Foundry evaluation wiring.

`evaluate_agent` is injected as a fake, so the dataset, the evaluation call, the console
summary, and the CI quality gate are all verified without contacting Foundry.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agent_framework import EvalItemResult, EvalResults, EvalScoreResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import dataset  # noqa: E402
from app.config import FoundrySettings, SandboxSettings, Settings  # noqa: E402
from app.evaluate import EVAL_NAME, passes_quality_gate, run_evaluation, summarize  # noqa: E402

SETTINGS = Settings(
    foundry=FoundrySettings(project_endpoint="https://example/api/projects/p", model_deployment="gpt-5"),
    sandbox=SandboxSettings(
        subscription_id="sub-id",
        resource_group="rg-test",
        sandbox_group="sbg-test",
        region="westus2",
        disk_image="python-3.14",
    ),
)


class RecordingEvaluate:
    """Captures the arguments `run_evaluation` passes to `evaluate_agent`."""

    def __init__(self, results: list[EvalResults] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.results = results or [EvalResults(provider="foundry", report_url="https://ai.azure.com/run/1")]

    async def __call__(self, **kwargs: Any) -> list[EvalResults]:
        self.calls.append(kwargs)
        return self.results


def test_dataset_is_consistent() -> None:
    assert len(dataset.queries()) == len(dataset.TASKS)
    assert len(dataset.expected_outputs()) == len(dataset.TASKS)
    tool_calls = dataset.expected_tool_calls()
    assert len(tool_calls) == len(dataset.TASKS)
    # Every task must force the agent through the sandbox.
    assert all(call[0].name == dataset.RUN_PYTHON for call in tool_calls)


async def test_run_evaluation_sends_the_dataset_to_foundry() -> None:
    evaluate = RecordingEvaluate()
    agent = object()
    evaluators = object()

    results = await run_evaluation(SETTINGS, agent=agent, evaluators=evaluators, evaluate=evaluate)

    assert results == evaluate.results
    (call,) = evaluate.calls
    assert call["agent"] is agent
    assert call["evaluators"] is evaluators
    assert call["eval_name"] == EVAL_NAME
    assert call["queries"] == dataset.queries()
    assert call["expected_output"] == dataset.expected_outputs()
    assert len(call["expected_tool_calls"]) == len(dataset.TASKS)


def test_summary_includes_the_foundry_report_link() -> None:
    results = [
        EvalResults(
            provider="foundry",
            result_counts={"passed": 5, "failed": 0, "total": 5},
            per_evaluator={"task_adherence": 4.6, "tool_call_accuracy": 5.0},
            report_url="https://ai.azure.com/run/1",
        )
    ]

    summary = summarize(results)

    assert "5/5 evaluation items passed" in summary
    assert "task_adherence: 4.6" in summary
    assert "https://ai.azure.com/run/1" in summary


def make_item(status: str, score: float) -> EvalItemResult:
    return EvalItemResult(item_id="item-1", status=status, scores=[EvalScoreResult(name="relevance", score=score)])


def test_quality_gate_passes_when_all_scores_meet_the_threshold() -> None:
    results = [EvalResults(provider="foundry", items=[make_item("pass", 4.0)])]

    assert passes_quality_gate(results, minimum_score=3.0)


def test_quality_gate_fails_on_a_low_score() -> None:
    results = [EvalResults(provider="foundry", items=[make_item("pass", 2.0)])]

    assert not passes_quality_gate(results, minimum_score=3.0)


def test_quality_gate_fails_on_a_failed_item() -> None:
    results = [EvalResults(provider="foundry", items=[make_item("fail", 5.0)])]

    assert not passes_quality_gate(results, minimum_score=3.0)
