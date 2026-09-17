# Copyright (c) Microsoft. All rights reserved.
"""Tests for the agent definition and its sandbox-backed `run_python` tool.

A fake sandbox runner replaces the Container Apps client, so these tests run offline while
still exercising the real Agent Framework tool wiring.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import AGENT_NAME, INSTRUCTIONS, build_agent, build_code_tools  # noqa: E402
from app.config import FoundrySettings, SandboxSettings, Settings  # noqa: E402
from app.sandbox import CodeExecution  # noqa: E402

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


class FakeRunner:
    """Records the code the model asked to run and returns a canned result."""

    def __init__(self, execution: CodeExecution | None = None) -> None:
        self.execution = execution or CodeExecution(exit_code=0, stdout="42\n", stderr="")
        self.executed: list[str] = []

    async def run_python(self, code: str) -> CodeExecution:
        self.executed.append(code)
        return self.execution


class FakeChatClient:
    """Minimal stand-in for `FoundryChatClient`; never called by these tests."""

    async def get_response(self, *args: object, **kwargs: object):  # pragma: no cover - unused
        raise NotImplementedError

    async def get_streaming_response(self, *args: object, **kwargs: object):  # pragma: no cover - unused
        raise NotImplementedError


def test_run_python_tool_is_exposed_with_a_usable_schema() -> None:
    (run_python,) = build_code_tools(FakeRunner())

    assert run_python.name == "run_python"
    assert "sandbox" in (run_python.description or "").lower()
    parameters = run_python.parameters()
    assert parameters["required"] == ["code"]
    assert parameters["properties"]["code"]["type"] == "string"


async def test_invoking_the_tool_runs_the_code_in_the_sandbox() -> None:
    runner = FakeRunner()
    (run_python,) = build_code_tools(runner)

    output = await run_python.invoke(arguments={"code": "print(6 * 7)"})

    assert runner.executed == ["print(6 * 7)"]
    assert "exit_code: 0" in output[0].text
    assert "42" in output[0].text


async def test_tool_output_surfaces_errors_so_the_agent_can_retry() -> None:
    runner = FakeRunner(CodeExecution(exit_code=1, stdout="", stderr="SyntaxError: invalid syntax"))
    (run_python,) = build_code_tools(runner)

    output = await run_python.invoke(arguments={"code": "print("})

    assert "exit_code: 1" in output[0].text
    assert "SyntaxError" in output[0].text


def test_build_agent_registers_the_sandbox_tool() -> None:
    agent = build_agent(SETTINGS, FakeRunner(), client=FakeChatClient())

    assert agent.name == AGENT_NAME
    assert [t.name for t in agent.default_options["tools"]] == ["run_python"]
    # The agent must be told to compute via the sandbox rather than answering from memory.
    assert "run_python" in (agent.default_options.get("instructions") or INSTRUCTIONS)
