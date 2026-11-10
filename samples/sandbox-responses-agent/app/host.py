# Copyright (c) Microsoft. All rights reserved.
"""Provision a sandbox, start the Responses server inside it, and expose its port."""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import Settings

FOUNDRY_SCOPE = "https://ai.azure.com/.default"
SAMPLE_LABEL = {"sample": "sandbox-responses-agent"}
WORKSPACE = "/workspace"
SECRETS_DIR = "/workspace/.secrets"
REMOTE_SERVER = f"{WORKSPACE}/sandbox_server.py"
REMOTE_REQUIREMENTS = f"{WORKSPACE}/requirements-sandbox.txt"
REMOTE_CONFIG = f"{WORKSPACE}/server-config.json"
REMOTE_TOKEN = f"{SECRETS_DIR}/foundry-token.json"
REMOTE_TOKEN_STAGING = f"{SECRETS_DIR}/foundry-token.json.new"
REMOTE_API_KEY = f"{SECRETS_DIR}/api-key"
REMOTE_LOG = "/tmp/sandbox-responses-agent.log"
SOURCE_DIR = Path(__file__).resolve().parent

TokenProvider = Callable[[], Awaitable[tuple[str, int]]]
ClientFactory = Callable[[Settings], tuple[Any, Any]]


@dataclass(frozen=True)
class Deployment:
    sandbox_id: str
    url: str
    api_key: str
    token_expires_on: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, raw: str) -> Deployment:
        return cls(**json.loads(raw))


async def default_token_provider() -> tuple[str, int]:
    """Acquire a short-lived Foundry token; host credentials never leave this machine."""
    from azure.identity.aio import DefaultAzureCredential

    credential = DefaultAzureCredential()
    try:
        token = await credential.get_token(FOUNDRY_SCOPE)
        return token.token, token.expires_on
    finally:
        await credential.close()


def default_client_factory(settings: Settings) -> tuple[Any, Any]:
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


def build_port_request(settings: Settings) -> Any:
    """Expose the server over HTTPS; the server itself enforces the API key."""
    from azure.containerapps.sandbox import (
        AddPortRequest,
        PortAuthConfig,
        PortIpAccessControl,
        PortIpAccessControlRule,
    )

    acl = None
    if settings.allowed_cidrs:
        acl = PortIpAccessControl(
            default_action="Deny",
            rules=[
                PortIpAccessControlRule(
                    name="allowed-clients",
                    action="Allow",
                    priority=10,
                    source_cidrs=list(settings.allowed_cidrs),
                )
            ],
        )
    return AddPortRequest(port=settings.port, auth=PortAuthConfig(anonymous=True), ip_access_control=acl)


class SandboxResponsesHost:
    """Manage a long-running Agent Framework Responses endpoint in a sandbox."""

    def __init__(
        self,
        settings: Settings,
        *,
        token_provider: TokenProvider = default_token_provider,
        client_factory: ClientFactory = default_client_factory,
        port_request_factory: Callable[[Settings], Any] = build_port_request,
        readiness_timeout: float = 120,
        poll_interval: float = 2,
    ) -> None:
        self._settings = settings
        self._token_provider = token_provider
        self._client_factory = client_factory
        self._port_request_factory = port_request_factory
        self._readiness_timeout = readiness_timeout
        self._poll_interval = poll_interval

    async def start(self) -> Deployment:
        """Create the sandbox and return its public Responses endpoint; delete it on any failure."""
        group_client, credential = self._client_factory(self._settings)
        sandbox = None
        started = False
        try:
            poller = await group_client.begin_create_sandbox(
                disk=self._settings.sandbox_disk_image,
                labels=SAMPLE_LABEL,
                auto_suspend_seconds=self._settings.idle_suspend_seconds,
            )
            sandbox = await poller.result()
            api_key = secrets.token_urlsafe(32)
            expires_on = await self._upload_runtime(sandbox, api_key)

            await _check(
                sandbox,
                f"python3 -m pip install --disable-pip-version-check -q -r {REMOTE_REQUIREMENTS}",
                "Sandbox dependency installation failed",
            )
            await _check(
                sandbox,
                f"nohup python3 {REMOTE_SERVER} --config {REMOTE_CONFIG} --token {REMOTE_TOKEN} "
                f"--api-key {REMOTE_API_KEY} > {REMOTE_LOG} 2>&1 < /dev/null &",
                "Failed to start the Responses server",
            )
            await self._wait_until_ready(sandbox)

            ports = await sandbox.update_ports([self._port_request_factory(self._settings)])
            url = next((p.url for p in ports if p.port == self._settings.port and p.url), None)
            if not url:
                raise RuntimeError(f"Sandbox did not return a URL for port {self._settings.port}")

            started = True
            return Deployment(sandbox.sandbox_id, url.rstrip("/"), api_key, expires_on)
        finally:
            if sandbox is not None and not started:
                await sandbox.delete()
            await group_client.close()
            await credential.close()

    async def refresh(self, sandbox_id: str) -> int:
        """Resume the sandbox if suspended and atomically replace its Foundry token."""
        group_client, credential = self._client_factory(self._settings)
        try:
            sandbox = group_client.get_sandbox_client(sandbox_id)
            await sandbox.ensure_running()
            token, expires_on = await self._token_provider()
            await sandbox.write_file(REMOTE_TOKEN_STAGING, json.dumps({"token": token, "expires_on": expires_on}))
            await _check(
                sandbox,
                f"chmod 600 {REMOTE_TOKEN_STAGING} && mv -f {REMOTE_TOKEN_STAGING} {REMOTE_TOKEN}",
                "Failed to replace the sandbox token",
            )
            await self._wait_until_ready(sandbox)
            return expires_on
        finally:
            await group_client.close()
            await credential.close()

    async def stop(self, sandbox_id: str) -> None:
        group_client, credential = self._client_factory(self._settings)
        try:
            await group_client.delete_sandbox(sandbox_id)
        finally:
            await group_client.close()
            await credential.close()

    async def _upload_runtime(self, sandbox: Any, api_key: str) -> int:
        token, expires_on = await self._token_provider()
        config = {
            "project_endpoint": self._settings.foundry_project_endpoint,
            "model_deployment": self._settings.foundry_model_deployment,
            "port": self._settings.port,
        }
        await sandbox.mkdir(WORKSPACE)
        await sandbox.mkdir(SECRETS_DIR)
        await _check(sandbox, f"chmod 700 {SECRETS_DIR}", "Failed to protect the secrets directory")
        await sandbox.write_file(REMOTE_SERVER, SOURCE_DIR.joinpath("sandbox_server.py").read_text(encoding="utf-8"))
        await sandbox.write_file(
            REMOTE_REQUIREMENTS, SOURCE_DIR.parent.joinpath("requirements-sandbox.txt").read_text(encoding="utf-8")
        )
        await sandbox.write_file(REMOTE_CONFIG, json.dumps(config))
        await sandbox.write_file(REMOTE_TOKEN, json.dumps({"token": token, "expires_on": expires_on}))
        await sandbox.write_file(REMOTE_API_KEY, api_key)
        await _check(sandbox, f"chmod 600 {REMOTE_TOKEN} {REMOTE_API_KEY}", "Failed to protect sandbox secrets")
        return expires_on

    async def _wait_until_ready(self, sandbox: Any) -> None:
        """Probe readiness from inside the sandbox so ingress IP rules cannot block the check."""
        probe = (
            "python3 -c \"import urllib.request; "
            f"urllib.request.urlopen('http://127.0.0.1:{self._settings.port}/readiness', timeout=5)\""
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._readiness_timeout
        while True:
            result = await sandbox.exec(probe)
            if result.exit_code == 0:
                return
            if loop.time() >= deadline:
                log = await sandbox.exec(f"tail -n 50 {REMOTE_LOG}")
                raise RuntimeError(f"Responses server did not become ready:\n{log.stdout}{log.stderr}")
            await asyncio.sleep(self._poll_interval)


async def _check(sandbox: Any, command: str, message: str) -> Any:
    result = await sandbox.exec(command, working_directory=WORKSPACE)
    if result.exit_code != 0:
        raise RuntimeError(f"{message}:\n{result.stderr}")
    return result
