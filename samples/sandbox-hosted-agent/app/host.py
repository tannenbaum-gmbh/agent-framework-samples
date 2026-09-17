# Copyright (c) Microsoft. All rights reserved.
"""Provision a sandbox and execute the Agent Framework process inside it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import Settings

FOUNDRY_SCOPE = "https://ai.azure.com/.default"
WORKSPACE = "/workspace"
REMOTE_AGENT = f"{WORKSPACE}/sandbox_agent.py"
REMOTE_REQUIREMENTS = f"{WORKSPACE}/requirements-sandbox.txt"
REMOTE_REQUEST = f"{WORKSPACE}/request.json"
REMOTE_TOKEN = "/tmp/foundry-token.json"

TokenProvider = Callable[[], Awaitable[tuple[str, int]]]


async def default_token_provider() -> tuple[str, int]:
    """Acquire a short-lived Foundry token without copying host credentials."""
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    try:
        token = await credential.get_token(FOUNDRY_SCOPE)
        return token.token, token.expires_on
    finally:
        await credential.close()


def default_client_factory(settings: Settings) -> Any:
    from azure.containerapps.sandbox import endpoint_for_region
    from azure.containerapps.sandbox.aio import SandboxGroupClient
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    client = SandboxGroupClient(
        endpoint_for_region(settings.sandbox_region),
        credential,
        subscription_id=settings.subscription_id,
        resource_group=settings.resource_group,
        sandbox_group=settings.sandbox_group,
    )
    return client, credential


class SandboxHostedAgent:
    """Upload and invoke a trusted agent process in an ephemeral sandbox."""

    def __init__(
        self,
        settings: Settings,
        *,
        token_provider: TokenProvider = default_token_provider,
        client_factory: Callable[[Settings], tuple[Any, Any]] = default_client_factory,
    ) -> None:
        self._settings = settings
        self._token_provider = token_provider
        self._client_factory = client_factory

    async def run(self, question: str) -> str:
        group_client, credential = self._client_factory(self._settings)
        sandbox = None
        try:
            poller = await group_client.begin_create_sandbox(
                disk=self._settings.sandbox_disk_image,
                labels={"sample": "sandbox-hosted-agent"},
            )
            sandbox = await poller.result()
            await sandbox.mkdir(WORKSPACE)
            await self._upload_runtime(sandbox, question)

            install = await sandbox.exec(
                f"python3 -m pip install --disable-pip-version-check -r {REMOTE_REQUIREMENTS}",
                working_directory=WORKSPACE,
            )
            if install.exit_code != 0:
                raise RuntimeError(f"Sandbox dependency installation failed:\n{install.stderr}")

            result = await sandbox.exec(
                f"python3 {REMOTE_AGENT} --request {REMOTE_REQUEST} --token {REMOTE_TOKEN}",
                working_directory=WORKSPACE,
            )
            if result.exit_code != 0:
                raise RuntimeError(f"Sandbox agent failed:\n{result.stderr}")
            return result.stdout.strip()
        finally:
            if sandbox is not None:
                try:
                    await sandbox.exec(f"rm -f {REMOTE_REQUEST} {REMOTE_TOKEN}")
                finally:
                    await sandbox.delete()
            await group_client.close()
            await credential.close()

    async def _upload_runtime(self, sandbox: Any, question: str) -> None:
        token, expires_on = await self._token_provider()
        source = Path(__file__).with_name("sandbox_agent.py").read_text(encoding="utf-8")
        requirements = Path(__file__).parents[1].joinpath("requirements-sandbox.txt").read_text(encoding="utf-8")
        request = {
            "project_endpoint": self._settings.foundry_project_endpoint,
            "model_deployment": self._settings.foundry_model_deployment,
            "question": question,
        }
        await sandbox.write_file(REMOTE_AGENT, source)
        await sandbox.write_file(REMOTE_REQUIREMENTS, requirements)
        await sandbox.write_file(REMOTE_REQUEST, json.dumps(request))
        await sandbox.write_file(REMOTE_TOKEN, json.dumps({"token": token, "expires_on": expires_on}))
        await sandbox.exec(f"chmod 600 {REMOTE_REQUEST} {REMOTE_TOKEN}")