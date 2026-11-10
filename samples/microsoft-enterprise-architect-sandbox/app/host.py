"""Supervise a Responses service in the existing sandbox group."""

from __future__ import annotations

import asyncio
import json
import signal
from contextlib import AsyncExitStack
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

import httpx
from azure.containerapps.sandbox import endpoint_for_region
from azure.containerapps.sandbox.aio import SandboxClient, SandboxGroupClient
from azure.core.credentials import AccessToken
from azure.identity.aio import DefaultAzureCredential

from .config import Settings, get_settings
from .tokens import FOUNDRY_SCOPE

WORKSPACE = "/workspace/mea-service"
TOKEN_PATH = f"{WORKSPACE}/tokens.json"
RUNTIME_FILES = (
    "app/__init__.py", "app/config.py", "app/tokens.py", "app/agent.py", "app/service.py",
    "requirements-sandbox.txt",
)


class TokenSource(Protocol):
    async def get_token(self, *scopes: str) -> AccessToken: ...


async def upload_tokens(
    sandbox: SandboxClient, foundry: TokenSource,
) -> None:
    model_token = await foundry.get_token(FOUNDRY_SCOPE)
    content = json.dumps({
        "foundry": {"token": model_token.token, "expires_on": model_token.expires_on},
    })
    await sandbox.write_file(f"{TOKEN_PATH}.new", content, mode="0600")
    result = await sandbox.exec(f"mv -f {TOKEN_PATH}.new {TOKEN_PATH}")
    if result.exit_code != 0:
        raise RuntimeError("Could not atomically refresh sandbox tokens.")


async def check_command(sandbox: SandboxClient, command: str, operation: str) -> None:
    result = await sandbox.exec(command, working_directory=WORKSPACE)
    if result.exit_code != 0:
        raise RuntimeError(f"{operation} failed:\n{result.stderr}")


async def probe_readiness(url: str, api_key: str) -> None:
    async with httpx.AsyncClient(
        headers={"X-API-Key": api_key}, timeout=10,
    ) as client:
        for _ in range(60):
            try:
                response = await client.get(f"{url}/readiness")
            except httpx.TransportError:
                pass
            else:
                if response.status_code == 200:
                    return
                if response.status_code not in (502, 503, 504):
                    response.raise_for_status()
            await asyncio.sleep(2)
    raise RuntimeError("Sandbox Responses API did not become ready within two minutes.")


async def wait_ready(url: str, api_key: str) -> None:
    try:
        await asyncio.wait_for(probe_readiness(url, api_key), timeout=120)
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Sandbox Responses API did not become ready within two minutes.") from exc


async def serve(
    settings: Settings,
    group: SandboxGroupClient,
    foundry: TokenSource,
    stop: asyncio.Event,
) -> None:
    sandbox = None
    try:
        poller = await group.begin_create_sandbox(
            disk=settings.sandbox_disk_image,
            labels={"sample": "microsoft-enterprise-architect-sandbox"},
            auto_suspend_seconds=3600,
        )
        sandbox = await poller.result()
        root = Path(__file__).parents[1]
        for name in RUNTIME_FILES:
            await sandbox.write_file(
                f"{WORKSPACE}/{name}", root.joinpath(name).read_text(encoding="utf-8"),
            )
        await sandbox.write_file(
            f"{WORKSPACE}/config.json", json.dumps(asdict(settings.runtime)), mode="0600",
        )
        await upload_tokens(sandbox, foundry)
        await check_command(
            sandbox,
            "python3 -m pip install --disable-pip-version-check -r requirements-sandbox.txt",
            "Dependency installation",
        )
        await upload_tokens(sandbox, foundry)
        await check_command(
            sandbox,
            "nohup env OTEL_SDK_DISABLED=true OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false "
            "python3 -m app.service --config config.json --tokens tokens.json "
            "> service.log 2>&1 < /dev/null &",
            "Service startup",
        )
        # ACA ingress is anonymous; the application rejects every request without its key.
        port = await sandbox.add_port(8080, anonymous=True)
        if not port.url or not port.url.startswith("https://"):
            raise RuntimeError("Sandbox ingress did not return an HTTPS URL.")
        url = port.url.rstrip("/")
        await wait_ready(url, settings.runtime.api_key)
        print(f"Responses API ready: {url}\nKeep this launcher running; Ctrl+C deletes its sandbox.", flush=True)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                await upload_tokens(sandbox, foundry)
                await wait_ready(url, settings.runtime.api_key)
    finally:
        if sandbox is not None:
            try:
                await sandbox.delete()
            finally:
                await sandbox.close()


async def main() -> None:
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    async with AsyncExitStack() as stack:
        foundry = await stack.enter_async_context(DefaultAzureCredential())
        # Verify model access before allocating billable compute.
        await foundry.get_token(FOUNDRY_SCOPE)
        group = await stack.enter_async_context(SandboxGroupClient(
            endpoint_for_region(settings.sandbox_region),
            foundry,
            subscription_id=settings.subscription_id,
            resource_group=settings.resource_group,
            sandbox_group=settings.sandbox_group,
        ))
        await serve(settings, group, foundry, stop)


if __name__ == "__main__":
    asyncio.run(main())
