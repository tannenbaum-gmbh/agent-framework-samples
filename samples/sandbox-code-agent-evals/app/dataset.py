# Copyright (c) Microsoft. All rights reserved.
"""The evaluation dataset: questions the agent can only answer by executing code."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_framework import ExpectedToolCall

RUN_PYTHON = "run_python"


@dataclass(frozen=True)
class EvalTask:
    """One evaluation case."""

    query: str
    expected_output: str
    expected_tool_calls: list[ExpectedToolCall] = field(default_factory=lambda: [ExpectedToolCall(RUN_PYTHON)])


# Every task is deliberately computation-heavy: a model that guesses instead of running
# code in the sandbox will produce a wrong or unverifiable answer, which the evaluators
# surface as low task-adherence and tool-call-accuracy scores.
TASKS: tuple[EvalTask, ...] = (
    EvalTask(
        query="What is the 200th Fibonacci number, with F(1) = F(2) = 1?",
        expected_output="173402521172797813159685037284371942044301",
    ),
    EvalTask(
        query="How many prime numbers are there below 1,000,000?",
        expected_output="78498",
    ),
    EvalTask(
        query=(
            "Write the CSV rows 'region,sales' / 'north,120' / 'south,340' / 'north,55' / 'east,210' "
            "to a file, then tell me the total sales for the 'north' region."
        ),
        expected_output="175",
    ),
    EvalTask(
        query="What are the last 8 digits of 2 raised to the power of 1000?",
        expected_output="68069376",
    ),
    EvalTask(
        query=(
            "Given the list [14, 3, 27, 8, 8, 91, 42, 3, 60], what is the median and the "
            "population standard deviation, each rounded to two decimals?"
        ),
        expected_output="median 14.0, population standard deviation 28.74",
    ),
)


def queries() -> list[str]:
    """The user questions handed to the agent under evaluation."""
    return [task.query for task in TASKS]


def expected_outputs() -> list[str]:
    """Ground-truth answers, used by output-based evaluators."""
    return [task.expected_output for task in TASKS]


def expected_tool_calls() -> list[list[ExpectedToolCall]]:
    """Tool calls each task must trigger, used by the tool-call-accuracy evaluator."""
    return [list(task.expected_tool_calls) for task in TASKS]
