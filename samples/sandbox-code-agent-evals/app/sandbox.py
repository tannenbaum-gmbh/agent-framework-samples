# Copyright (c) Microsoft. All rights reserved.
"""Runs agent-generated Python inside an Azure Container Apps sandbox (preview).

Container Apps Sandboxes give each agent a microVM-isolated Linux machine with its own
filesystem and process space, so code produced by a model never touches the host that
runs this sample. See https://learn.microsoft.com/azure/container-apps/sandboxes-overview

A single :class:`SandboxCodeRunner` owns one sandbox, created lazily on first use and
reused for every subsequent snippet, which keeps files written by one step available to
the next - the sandbox behaves like a persistent notebook kernel.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any, Protocol

from .config import SandboxSettings

WORKSPACE = "/workspace"
"""Working directory used for every execution, so relative paths are stable across calls."""

MAX_OUTPUT_CHARS = 4000
"""Model context is finite; long stdout/stderr is truncated before reaching the agent."""


@dataclass(frozen=True)
class CodeExecution:
    """Result of running a snippet in the sandbox."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        """Whether the interpreter exited cleanly."""
        return self.exit_code == 0

    def to_tool_output(self) -> str:
        """Render the result as the compact text handed back to the model."""
        parts = [f"exit_code: {self.exit_code}"]
        parts.append(f"stdout:\n{self.stdout}" if self.stdout else "stdout: (empty)")
        if self.stderr:
            parts.append(f"stderr:\n{self.stderr}")
        return "\n".join(parts)


class SandboxGroupClientFactory(Protocol):
    """Creates the async sandbox group client. Overridden in tests to avoid Azure calls."""

    def __call__(self, settings: SandboxSettings) -> Any: ...


def _truncate(value: str | None) -> str:
    text = value or ""
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return f"{text[:MAX_OUTPUT_CHARS]}\n... [truncated, {len(text) - MAX_OUTPUT_CHARS} more characters]"


class _DefaultSandboxGroupClient:
    def __init__(self, settings: SandboxSettings) -> None:
        from azure.containerapps.sandbox import endpoint_for_region
        from azure.containerapps.sandbox.aio import SandboxGroupClient
        from azure.identity.aio import DefaultAzureCredential

        self._credential = DefaultAzureCredential()
        self._client = SandboxGroupClient(
            endpoint_for_region(settings.region),
            self._credential,
            subscription_id=settings.subscription_id,
            resource_group=settings.resource_group,
            sandbox_group=settings.sandbox_group,
        )

    async def begin_create_sandbox(self, **kwargs: Any) -> Any:
        return await self._client.begin_create_sandbox(**kwargs)

    async def close(self) -> None:
        try:
            await self._client.close()
        finally:
            await self._credential.close()


def _default_client_factory(settings: SandboxSettings) -> Any:
    # Imported lazily so the Azure SDKs are only needed when actually talking to a
    # sandbox group, keeping unit tests lightweight and offline.
    return _DefaultSandboxGroupClient(settings)


class SandboxCodeRunner:
    """Lazily provisions one Container Apps sandbox and executes Python snippets in it."""

    def __init__(
        self,
        settings: SandboxSettings,
        *,
        client_factory: SandboxGroupClientFactory | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory or _default_client_factory
        self._group_client: Any | None = None
        self._sandbox: Any | None = None
        # Tool calls may be issued concurrently by the agent; only one of them may
        # provision the shared sandbox.
        self._lock = asyncio.Lock()

    @property
    def sandbox_id(self) -> str | None:
        """ID of the active sandbox, or ``None`` when none has been created yet."""
        return getattr(self._sandbox, "sandbox_id", None)

    async def _ensure_sandbox(self) -> Any:
        if self._sandbox is not None:
            return self._sandbox
        async with self._lock:
            if self._sandbox is None:
                self._group_client = self._client_factory(self._settings)
                poller = await self._group_client.begin_create_sandbox(
                    disk=self._settings.disk_image,
                    labels={"sample": "sandbox-code-agent-evals"},
                )
                self._sandbox = await poller.result()
                await self._sandbox.mkdir(WORKSPACE)
        return self._sandbox

    async def run_python(self, code: str) -> CodeExecution:
        """Write ``code`` to a file inside the sandbox and execute it with ``python3``."""
        sandbox = await self._ensure_sandbox()
        # The snippet is written to disk rather than interpolated into the shell command:
        # the sandbox `exec` API runs a shell, so untrusted model output must never be
        # part of the command string itself.
        script_path = f"/tmp/cell-{secrets.token_hex(8)}.py"
        await sandbox.write_file(script_path, code)
        try:
            result = await sandbox.exec(f"python3 {script_path}", working_directory=WORKSPACE)
        finally:
            try:
                await sandbox.exec(f"rm -f {script_path}")
            except Exception:
                # Best-effort cleanup; avoid masking the original failure.
                pass
        return CodeExecution(
            exit_code=result.exit_code,
            stdout=_truncate(result.stdout),
            stderr=_truncate(result.stderr),
        )

    async def aclose(self) -> None:
        """Delete the sandbox and release the underlying clients."""
        sandbox, group_client = self._sandbox, self._group_client
        self._sandbox, self._group_client = None, None
        if sandbox is not None:
            await sandbox.delete()
        if group_client is not None:
            await group_client.close()
