# Copyright (c) Microsoft. All rights reserved.
"""Tests for the Foundry evaluation wiring.

`evaluate_agent` is injected as a fake, so the dataset, the evaluation call, the console
summary, and the CI quality gate are all verified without contacting Foundry.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from agent_framework import EvalItemResult, EvalResults, EvalScoreResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import dataset  # noqa: E402
from app import evaluate as evaluation  # noqa: E402
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


@pytest.mark.parametrize("status", ["timeout", "failed", "canceled", "in_progress"])
def test_quality_gate_rejects_incomplete_results(status: str) -> None:
    assert not passes_quality_gate([EvalResults(provider="foundry", status=status)])


def test_timeout_summary_identifies_remote_run() -> None:
    result = EvalResults(provider="foundry", status="timeout", eval_id="eval-1", run_id="run-1")

    summary = summarize([result])

    assert "Evaluation status: timeout" in summary
    assert "eval_id=eval-1, run_id=run-1" in summary
    assert "remote run may still finish" in summary


def test_evaluator_does_not_override_polling_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_framework import foundry

    factory = Mock()
    monkeypatch.setattr(foundry, "FoundryEvals", factory)
    client = object()

    evaluation.build_evaluators(SETTINGS, client=client)

    assert "timeout" not in factory.call_args.kwargs
    assert factory.call_args.kwargs["client"] is client


async def test_run_evaluation_builds_bundle_without_polling_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = Mock(return_value=object())
    monkeypatch.setattr(evaluation, "build_evaluators", factory)

    await run_evaluation(SETTINGS, agent=object(), evaluate=RecordingEvaluate())

    factory.assert_called_once_with(SETTINGS)


async def test_evaluation_deadline_cancels_await_and_cleans_up_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canceled = asyncio.Event()
    runner = Mock(aclose=AsyncMock())
    monkeypatch.setattr(evaluation, "SandboxCodeRunner", Mock(return_value=runner))
    monkeypatch.setattr(evaluation, "build_agent", Mock(return_value=object()))

    async def stalled_evaluation(**_kwargs: Any) -> list[EvalResults]:
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()
        return []

    with pytest.raises(asyncio.TimeoutError):
        await run_evaluation(
            SETTINGS, evaluators=object(), evaluate=stalled_evaluation, timeout=0.01,
        )

    assert canceled.is_set()
    runner.aclose.assert_awaited_once()


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
async def test_run_evaluation_rejects_invalid_deadline(timeout: float) -> None:
    evaluate = AsyncMock()
    with pytest.raises(ValueError, match="finite positive"):
        await run_evaluation(SETTINGS, agent=object(), evaluators=object(), evaluate=evaluate, timeout=timeout)
    evaluate.assert_not_awaited()


async def test_logs_evaluation_result_before_sandbox_cleanup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=evaluation.__name__)
    runner = Mock()

    async def close() -> None:
        assert "Evaluation await returned" in caplog.text
        assert "status=completed eval_id=eval-1 run_id=run-1" in caplog.text
        assert "Deleting sandbox" in caplog.text
        assert "Sandbox cleanup finished" not in caplog.text

    runner.aclose = AsyncMock(side_effect=close)
    monkeypatch.setattr(evaluation, "SandboxCodeRunner", Mock(return_value=runner))
    monkeypatch.setattr(evaluation, "build_agent", Mock(return_value=object()))
    result = EvalResults(provider="foundry", eval_id="eval-1", run_id="run-1")

    assert await run_evaluation(
        SETTINGS, evaluators=object(), evaluate=RecordingEvaluate([result]),
    ) == [result]

    runner.aclose.assert_awaited_once()
    assert "Sandbox cleanup finished" in caplog.text


async def test_evaluation_exception_still_cleans_up_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Mock(aclose=AsyncMock())
    monkeypatch.setattr(evaluation, "SandboxCodeRunner", Mock(return_value=runner))
    monkeypatch.setattr(evaluation, "build_agent", Mock(return_value=object()))

    with pytest.raises(TimeoutError, match="request stalled"):
        await run_evaluation(
            SETTINGS, evaluators=object(), evaluate=AsyncMock(side_effect=TimeoutError("request stalled")),
        )

    runner.aclose.assert_awaited_once()


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
async def test_cli_rejects_invalid_timeout(timeout: str) -> None:
    with pytest.raises(SystemExit) as error:
        await evaluation.main(["--eval-timeout", timeout])

    assert error.value.code == 2


async def test_cli_passes_evaluation_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    result = EvalResults(provider="foundry", items=[make_item("pass", 4.0)])
    run = AsyncMock(return_value=[result])
    monkeypatch.setattr(evaluation, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(evaluation, "run_evaluation", run)

    assert await evaluation.main(["--eval-timeout", "900"]) == 0
    run.assert_awaited_once_with(SETTINGS, timeout=900.0)


async def test_cli_returns_failure_on_polling_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    result = EvalResults(provider="foundry", status="timeout", eval_id="eval-1", run_id="run-1")
    monkeypatch.setattr(evaluation, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(evaluation, "run_evaluation", AsyncMock(return_value=[result]))

    assert await evaluation.main([]) == 1


async def test_cli_reports_evaluation_deadline_expiry(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(evaluation, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(evaluation, "run_evaluation", AsyncMock(side_effect=asyncio.TimeoutError))

    assert await evaluation.main(["--eval-timeout", "900"]) == 1
    assert "Evaluation timed out (await deadline: 900.0s)" in caplog.text
    assert "remote run may still finish" in caplog.text
