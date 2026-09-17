# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.host import SandboxHostedAgent  # noqa: E402

SETTINGS = Settings(
    foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
    foundry_model_deployment="gpt-5",
    subscription_id="sub",
    resource_group="rg",
    sandbox_group="group",
    sandbox_region="westus2",
    sandbox_disk_image="python-3.14",
)


@dataclass
class ExecResult:
    exit_code: int = 0
    stdout: str = "Answer from sandbox\n"
    stderr: str = ""


class FakeSandbox:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.commands: list[str] = []
        self.deleted = False

    async def mkdir(self, path: str) -> None:
        assert path == "/workspace"

    async def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    async def exec(self, command: str, **kwargs: Any) -> ExecResult:
        self.commands.append(command)
        return ExecResult(stdout="Answer from sandbox\n" if "sandbox_agent.py" in command else "")

    async def delete(self) -> None:
        self.deleted = True


class FakePoller:
    def __init__(self, sandbox: FakeSandbox) -> None:
        self.sandbox = sandbox

    async def result(self) -> FakeSandbox:
        return self.sandbox


class Closeable:
    def __init__(self, sandbox: FakeSandbox | None = None) -> None:
        self.sandbox = sandbox
        self.closed = False
        self.create_calls: list[dict[str, Any]] = []

    async def begin_create_sandbox(self, **kwargs: Any) -> FakePoller:
        self.create_calls.append(kwargs)
        assert self.sandbox is not None
        return FakePoller(self.sandbox)

    async def close(self) -> None:
        self.closed = True


def test_agent_source_executes_in_sandbox_without_secrets_in_commands() -> None:
    asyncio.run(run_agent_source_scenario())


async def run_agent_source_scenario() -> None:
    sandbox = FakeSandbox()
    client = Closeable(sandbox)
    credential = Closeable()

    async def token_provider() -> tuple[str, int]:
        return "secret-access-token", 2_000_000_000

    hosted_agent = SandboxHostedAgent(
        SETTINGS,
        token_provider=token_provider,
        client_factory=lambda settings: (client, credential),
    )
    answer = await hosted_agent.run("question with 'shell syntax' $(oops)")

    assert answer == "Answer from sandbox"
    assert "from agent_framework import Agent" in sandbox.files["/workspace/sandbox_agent.py"]
    assert "shell syntax" in sandbox.files["/workspace/request.json"]
    assert "secret-access-token" in sandbox.files["/tmp/foundry-token.json"]
    assert all("secret-access-token" not in command and "shell syntax" not in command for command in sandbox.commands)
    assert sandbox.deleted
    assert client.closed
    assert credential.closed