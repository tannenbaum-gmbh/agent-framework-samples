from __future__ import annotations

import asyncio

import pytest

from app.workflow import ArchitectureWorkflow


async def test_draft_review_final_order_and_context() -> None:
    calls: list[tuple[str, str]] = []

    async def architect(prompt: str) -> str:
        calls.append(("architect", prompt))
        return "Draft: SEC-001" if len(calls) == 1 else "Final: SEC-001 corrected"

    async def documentation(prompt: str) -> str:
        calls.append(("documentation", prompt))
        return "Correction: use zones. https://learn.microsoft.com/azure/reliability/"

    result = await ArchitectureWorkflow(architect, documentation).run("Tier-1 platform")

    assert [name for name, _ in calls] == ["architect", "documentation", "architect"]
    assert result.question == "Tier-1 platform"
    assert result.draft == "Draft: SEC-001"
    assert result.documentation_review.startswith("Correction:")
    assert result.final_recommendation == "Final: SEC-001 corrected"
    assert all("Tier-1 platform" in prompt for _, prompt in calls)
    assert result.draft in calls[1][1]
    assert result.draft in calls[2][1]
    assert result.documentation_review in calls[2][1]


@pytest.mark.parametrize("failed_stage", ["draft", "review", "final"])
async def test_failure_stops_workflow(failed_stage: str) -> None:
    calls: list[str] = []

    async def architect(prompt: str) -> str:
        stage = "draft" if not calls else "final"
        calls.append(stage)
        if stage == failed_stage:
            raise RuntimeError("source unavailable")
        return "Architecture"

    async def documentation(prompt: str) -> str:
        calls.append("review")
        if failed_stage == "review":
            raise RuntimeError("source unavailable")
        return "Documentation"

    with pytest.raises(Exception, match="source unavailable"):
        await ArchitectureWorkflow(architect, documentation).run("Platform")
    expected = ["draft", "review", "final"]
    assert calls == expected[: expected.index(failed_stage) + 1]


@pytest.mark.parametrize("empty_stage", ["draft", "review", "final"])
async def test_empty_response_is_not_success(empty_stage: str) -> None:
    calls: list[str] = []

    async def architect(prompt: str) -> str:
        stage = "draft" if not calls else "final"
        calls.append(stage)
        return " " if stage == empty_stage else "Architecture"

    async def documentation(prompt: str) -> str:
        calls.append("review")
        return " " if empty_stage == "review" else "Documentation"

    with pytest.raises(Exception, match="returned no text"):
        await ArchitectureWorkflow(architect, documentation).run("Platform")
    assert len(calls) == ["draft", "review", "final"].index(empty_stage) + 1


async def test_blank_question_is_rejected_before_agent_calls() -> None:
    async def unexpected(prompt: str) -> str:
        pytest.fail("Agent must not be called")

    with pytest.raises(ValueError, match="question is required"):
        await ArchitectureWorkflow(unexpected, unexpected).run(" ")


async def test_concurrent_runs_do_not_share_context() -> None:
    async def echo(prompt: str) -> str:
        await asyncio.sleep(0)
        return prompt

    workflow = ArchitectureWorkflow(echo, echo)
    first, second = await asyncio.gather(
        workflow.run("WORKLOAD_ALPHA"), workflow.run("WORKLOAD_BETA")
    )
    assert "WORKLOAD_ALPHA" in first.final_recommendation
    assert "WORKLOAD_BETA" not in first.final_recommendation
    assert "WORKLOAD_BETA" in second.final_recommendation
    assert "WORKLOAD_ALPHA" not in second.final_recommendation
