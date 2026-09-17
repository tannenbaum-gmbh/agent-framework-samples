# Copyright (c) Microsoft. All rights reserved.
"""Builds the code-interpreter agent whose tool executes inside a Container Apps sandbox."""

from __future__ import annotations

from typing import Annotated, Any

from agent_framework import Agent, FunctionTool, tool
from pydantic import Field

from .config import Settings
from .sandbox import SandboxCodeRunner

AGENT_NAME = "sandbox-code-agent"

INSTRUCTIONS = (
    "You are a data and math assistant. You cannot calculate anything yourself: for every "
    "question that involves computation, data processing, or file handling, you must write "
    "Python and run it with the `run_python` tool, then answer using its output. "
    "Always `print()` the values you need — stdout is returned to you, and errors (stderr) are included when present. "
    "The sandbox is a Linux machine that persists between tool calls, so files you write in "
    "one call are still there in the next. If a snippet fails, read the error, fix the code, "
    "and run it again. Finish with a short, direct answer that states the result."
)


def build_code_tools(runner: SandboxCodeRunner) -> list[FunctionTool]:
    """Expose ``runner`` to the model as a `run_python` function tool."""

    async def run_python(
        code: Annotated[str, Field(description="Self-contained Python source to execute. Print anything you need.")],
    ) -> str:
        """Execute Python code in an isolated Linux sandbox and return its exit code, stdout, and stderr."""
        execution = await runner.run_python(code)
        return execution.to_tool_output()

    return [tool(run_python, name="run_python")]


def build_agent(
    settings: Settings,
    runner: SandboxCodeRunner,
    *,
    client: Any | None = None,
) -> Agent:
    """Create the sandboxed code agent backed by Microsoft Foundry.

    Args:
        settings: Foundry and sandbox configuration.
        runner: Sandbox runner backing the agent's `run_python` tool.
        client: Optional pre-built chat client. Tests pass a fake here so that no
            Azure resources are required.
    """
    if client is None:
        # Imported lazily so the FoundryChatClient (and its Azure SDK dependencies) is only
        # required when actually talking to Microsoft Foundry, keeping unit tests lightweight.
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential

        client = FoundryChatClient(
            project_endpoint=settings.foundry.project_endpoint,
            model=settings.foundry.model_deployment,
            credential=DefaultAzureCredential(),
        )

    return Agent(
        client=client,
        name=AGENT_NAME,
        description="Answers quantitative questions by writing and running Python in an Azure Container Apps sandbox.",
        instructions=INSTRUCTIONS,
        tools=build_code_tools(runner),
    )
