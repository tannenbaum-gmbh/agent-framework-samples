# Copyright (c) Microsoft. All rights reserved.
"""Provision a sandbox and execute the agent process inside it as an agent identity."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Settings

WORKSPACE = "/workspace"
REMOTE_AGENT = f"{WORKSPACE}/agent_framework_agent.py"
REMOTE_REQUIREMENTS = f"{WORKSPACE}/requirements-sandbox.txt"
REMOTE_REQUEST = f"{WORKSPACE}/request.json"


@dataclass(frozen=True)
class AgentRunResult:
    answer: str
    agent_identity: str
    blueprint: str
    user_agent: str


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


class AgentIdentitySandboxAgent:
    """Upload and invoke a trusted agent process that authenticates as an agent identity."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[Settings], tuple[Any, Any]] = default_client_factory,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory

    async def run(self, question: str, *, user_assertion: str) -> AgentRunResult:
        if not user_assertion.strip():
            raise ValueError("A signed-in user's blueprint-audience access token is required for OBO")
        group_client, credential = self._client_factory(self._settings)
        sandbox = None
        try:
            poller = await group_client.begin_create_sandbox(
                disk=self._settings.sandbox_disk_image,
                labels={
                    "sample": "agent365-enterprise-architect-workiq",
                    "agent-identity": self._settings.identity.agent_identity_client_id or "",
                },
            )
            sandbox = await poller.result()
            await sandbox.mkdir(WORKSPACE)
            await self._upload_runtime(sandbox, question, user_assertion)

            install = await sandbox.exec(
                f"python3 -m pip install --disable-pip-version-check -r {REMOTE_REQUIREMENTS}",
                working_directory=WORKSPACE,
            )
            if install.exit_code != 0:
                raise RuntimeError(f"Sandbox dependency installation failed:\n{install.stderr}")

            result = await sandbox.exec(
                f"python3 {REMOTE_AGENT} --request {REMOTE_REQUEST}",
                working_directory=WORKSPACE,
            )
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
            if result.exit_code != 0:
                raise RuntimeError(f"Sandbox agent failed:\n{result.stderr}")
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            return AgentRunResult(
                answer=payload["answer"],
                agent_identity=payload["agent_identity"],
                blueprint=payload["blueprint"],
                user_agent=payload["user_agent"],
            )
        finally:
            if sandbox is not None:
                try:
                    await sandbox.exec(f"rm -f {REMOTE_REQUEST}")
                finally:
                    await sandbox.delete()
            await group_client.close()
            await credential.close()

    async def _upload_runtime(self, sandbox: Any, question: str, user_assertion: str) -> None:
        source = Path(__file__).with_name("agent_framework_agent.py").read_text(encoding="utf-8")
        requirements = Path(__file__).parents[1].joinpath("requirements-sandbox.txt").read_text(encoding="utf-8")
        request = {
            "tenant_id": self._settings.identity.tenant_id,
            "blueprint_app_id": self._settings.identity.blueprint_app_id,
            "managed_identity_client_id": self._settings.identity.managed_identity_client_id,
            "project_endpoint": self._settings.foundry_project_endpoint,
            "model_deployment": self._settings.foundry_model_deployment,
            "agent_identity_client_id": self._settings.identity.agent_identity_client_id,
            "question": question,
            "observability_enabled": self._settings.observability_enabled,
            "user_assertion": user_assertion,
            "workiq_enable_actions": self._settings.workiq_enable_actions,
        }
        if self._settings.observability_enabled:
            request.update(
                observability_capture_content=self._settings.observability_capture_content,
                observability_sponsor_id=self._settings.observability_sponsor_id,
                observability_sponsor_name=self._settings.observability_sponsor_name,
                observability_sponsor_email=self._settings.observability_sponsor_email,
            )
            for name in ("OTEL_LOG_LEVEL", "A365_OBSERVABILITY_LOG_LEVEL"):
                request[name] = os.environ.get(
                    name, "INFO" if name == "OTEL_LOG_LEVEL" else "info|warn|error"
                )
            await sandbox.write_file(
                f"{WORKSPACE}/observability.py",
                Path(__file__).with_name("observability.py").read_text(encoding="utf-8"),
            )
        await sandbox.write_file(REMOTE_AGENT, source)
        await sandbox.write_file(
            f"{WORKSPACE}/agent_token.py", Path(__file__).with_name("agent_token.py").read_text(encoding="utf-8")
        )
        await sandbox.write_file(REMOTE_REQUIREMENTS, requirements)
        await sandbox.write_file(
            f"{WORKSPACE}/workiq.py", Path(__file__).with_name("workiq.py").read_text(encoding="utf-8")
        )
        permissions = await sandbox.exec(f"umask 077; touch {REMOTE_REQUEST}; chmod 600 {REMOTE_REQUEST}")
        if permissions.exit_code != 0:
            raise RuntimeError("Could not protect the sandbox request file")
        await sandbox.write_file(REMOTE_REQUEST, json.dumps(request))
        permissions = await sandbox.exec(f"chmod 600 {REMOTE_REQUEST}")
        if permissions.exit_code != 0:
            raise RuntimeError("Could not protect the sandbox request file")
