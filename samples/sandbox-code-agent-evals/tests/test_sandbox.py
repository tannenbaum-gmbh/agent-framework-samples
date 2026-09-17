# Copyright (c) Microsoft. All rights reserved.
"""Tests for the Container Apps sandbox code runner.

A fake sandbox group client stands in for `azure-containerapps-sandbox`, so these tests
exercise the real lifecycle, execution, and truncation logic without any Azure resources,
credentials, or network access.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import SandboxSettings  # noqa: E402
from app.sandbox import MAX_OUTPUT_CHARS, WORKSPACE, CodeExecution, SandboxCodeRunner  # noqa: E402

SETTINGS = SandboxSettings(
    subscription_id="sub-id",
    resource_group="rg-test",
    sandbox_group="sbg-test",
    region="westus2",
    disk_image="python-3.14",
)


@dataclass
class FakeExecResult:
    exit_code: int
    stdout: str
    stderr: str


class FakeSandbox:
    """Stands in for `azure.containerapps.sandbox.aio.SandboxClient`."""

    def __init__(self, *, stdout: str = "ok", stderr: str = "", exit_code: int = 0) -> None:
        self.sandbox_id = "sandbox-1"
        self.files: dict[str, str] = {}
        self.directories: list[str] = []
        self.commands: list[tuple[str, str | None]] = []
        self.deleted = False
        self._stdout, self._stderr, self._exit_code = stdout, stderr, exit_code

    async def mkdir(self, path: str, **kwargs: Any) -> None:
        self.directories.append(path)

    async def write_file(self, path: str, content: str, **kwargs: Any) -> None:
        self.files[path] = content

    async def exec(self, command: str, *, working_directory: str | None = None, **kwargs: Any) -> FakeExecResult:
        self.commands.append((command, working_directory))
        if command.startswith("rm -f"):
            return FakeExecResult(exit_code=0, stdout="", stderr="")
        return FakeExecResult(exit_code=self._exit_code, stdout=self._stdout, stderr=self._stderr)

    async def delete(self) -> None:
        self.deleted = True


class FakePoller:
    def __init__(self, sandbox: FakeSandbox) -> None:
        self._sandbox = sandbox

    async def result(self) -> FakeSandbox:
        return self._sandbox


class FakeGroupClient:
    """Stands in for `azure.containerapps.sandbox.aio.SandboxGroupClient`."""

    def __init__(self, sandbox: FakeSandbox, *, create_delay: float = 0.0) -> None:
        self.sandbox = sandbox
        self.create_calls: list[dict[str, Any]] = []
        self.closed = False
        self._create_delay = create_delay

    async def begin_create_sandbox(self, **kwargs: Any) -> FakePoller:
        self.create_calls.append(kwargs)
        if self._create_delay:
            await asyncio.sleep(self._create_delay)
        return FakePoller(self.sandbox)

    async def close(self) -> None:
        self.closed = True


def make_runner(sandbox: FakeSandbox, **client_kwargs: Any) -> tuple[SandboxCodeRunner, list[FakeGroupClient]]:
    clients: list[FakeGroupClient] = []

    def factory(settings: SandboxSettings) -> FakeGroupClient:
        assert settings is SETTINGS
        client = FakeGroupClient(sandbox, **client_kwargs)
        clients.append(client)
        return client

    return SandboxCodeRunner(SETTINGS, client_factory=factory), clients


async def test_run_python_writes_script_then_executes_it() -> None:
    sandbox = FakeSandbox(stdout="55\n")
    runner, clients = make_runner(sandbox)

    result = await runner.run_python("print(55)")

    assert result == CodeExecution(exit_code=0, stdout="55\n", stderr="")
    assert result.succeeded
    # The snippet must reach the sandbox as a file, never as part of the shell command.
    (script_path, code), = sandbox.files.items()
    assert code == "print(55)"
    run_command, working_directory = sandbox.commands[0]
    assert run_command == f"python3 {script_path}"
    assert working_directory == WORKSPACE
    # The temporary script is cleaned up afterwards.
    assert sandbox.commands[1][0] == f"rm -f {script_path}"
    assert clients[0].create_calls[0]["disk"] == "python-3.14"
    assert sandbox.directories == [WORKSPACE]


async def test_sandbox_is_created_once_and_reused() -> None:
    sandbox = FakeSandbox()
    runner, clients = make_runner(sandbox)

    await runner.run_python("print(1)")
    await runner.run_python("print(2)")

    assert len(clients) == 1
    assert len(clients[0].create_calls) == 1
    assert runner.sandbox_id == "sandbox-1"


async def test_concurrent_calls_create_only_one_sandbox() -> None:
    sandbox = FakeSandbox()
    runner, clients = make_runner(sandbox, create_delay=0.05)

    await asyncio.gather(*(runner.run_python(f"print({i})") for i in range(5)))

    assert len(clients[0].create_calls) == 1


async def test_failed_execution_is_reported_to_the_model() -> None:
    sandbox = FakeSandbox(exit_code=1, stdout="", stderr="NameError: name 'x' is not defined")
    runner, _ = make_runner(sandbox)

    result = await runner.run_python("print(x)")

    assert not result.succeeded
    output = result.to_tool_output()
    assert "exit_code: 1" in output
    assert "NameError" in output
    assert "stdout: (empty)" in output


async def test_long_output_is_truncated() -> None:
    sandbox = FakeSandbox(stdout="x" * (MAX_OUTPUT_CHARS + 500))
    runner, _ = make_runner(sandbox)

    result = await runner.run_python("print('x' * 4500)")

    assert "truncated" in result.stdout
    assert len(result.stdout) < MAX_OUTPUT_CHARS + 200


async def test_script_is_cleaned_up_even_when_execution_raises() -> None:
    sandbox = FakeSandbox()

    async def failing_exec(command: str, *, working_directory: str | None = None, **kwargs: Any) -> FakeExecResult:
        sandbox.commands.append((command, working_directory))
        if command.startswith("python3"):
            raise RuntimeError("sandbox unreachable")
        return FakeExecResult(exit_code=0, stdout="", stderr="")

    sandbox.exec = failing_exec  # type: ignore[method-assign]
    runner, _ = make_runner(sandbox)

    with pytest.raises(RuntimeError):
        await runner.run_python("print(1)")

    assert sandbox.commands[-1][0].startswith("rm -f")


async def test_aclose_deletes_the_sandbox_and_closes_the_client() -> None:
    sandbox = FakeSandbox()
    runner, clients = make_runner(sandbox)
    await runner.run_python("print(1)")

    await runner.aclose()

    assert sandbox.deleted
    assert clients[0].closed
    assert runner.sandbox_id is None
    # Closing twice must not fail, and must not delete a sandbox that no longer exists.
    await runner.aclose()


async def test_aclose_without_any_execution_is_a_no_op() -> None:
    sandbox = FakeSandbox()
    runner, clients = make_runner(sandbox)

    await runner.aclose()

    assert not sandbox.deleted
    assert clients == []
