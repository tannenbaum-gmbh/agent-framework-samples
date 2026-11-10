# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import asyncio
import json
import stat
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from agent_framework import Agent, BaseChatClient, ChatResponse, ChatResponseUpdate, Content, Message, ResponseStream
from azure.core.exceptions import ClientAuthenticationError

from app import cli, sandbox_server
from app.config import Settings, get_settings, parse_cidrs
from app.host import Deployment, SandboxResponsesHost, build_port_request

SETTINGS = Settings(
    foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
    foundry_model_deployment="gpt-5",
    subscription_id="sub",
    resource_group="rg",
    sandbox_group="group",
    sandbox_region="westus2",
)
TOKEN = "secret-access-token"
PUBLIC_URL = "https://sbx-123.example.azurecontainerapps.io/"


# ---------- configuration ----------


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name, value in {
        "FOUNDRY_PROJECT_ENDPOINT": SETTINGS.foundry_project_endpoint,
        "AZURE_SUBSCRIPTION_ID": "sub",
        "AZURE_RESOURCE_GROUP": "rg",
        "AZURE_SANDBOX_GROUP": "group",
        "AZURE_SANDBOX_REGION": "westus2",
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("FOUNDRY_MODEL_DEPLOYMENT", "SANDBOX_DISK_IMAGE", "SANDBOX_ALLOWED_CIDRS", "SANDBOX_IDLE_SUSPEND_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_settings_defaults(env: pytest.MonkeyPatch) -> None:
    assert get_settings() == SETTINGS


def test_settings_require_foundry_endpoint(env: pytest.MonkeyPatch) -> None:
    env.setenv("FOUNDRY_PROJECT_ENDPOINT", " ")
    with pytest.raises(RuntimeError, match="FOUNDRY_PROJECT_ENDPOINT"):
        get_settings()


def test_settings_overrides(env: pytest.MonkeyPatch) -> None:
    env.setenv("FOUNDRY_MODEL_DEPLOYMENT", "gpt-5.3-codex")
    env.setenv("SANDBOX_ALLOWED_CIDRS", "203.0.113.7/32, 10.0.0.0/8")
    env.setenv("SANDBOX_IDLE_SUSPEND_SECONDS", "600")
    settings = get_settings()
    assert settings.foundry_model_deployment == "gpt-5.3-codex"
    assert settings.allowed_cidrs == ("203.0.113.7/32", "10.0.0.0/8")
    assert settings.idle_suspend_seconds == 600


@pytest.mark.parametrize("raw", ["10.0.0.5/8", "not-a-cidr", ",".join(f"10.0.{i}.0/24" for i in range(11))])
def test_invalid_cidrs_are_rejected(raw: str) -> None:
    with pytest.raises(RuntimeError, match="SANDBOX_ALLOWED_CIDRS"):
        parse_cidrs(raw)


def test_port_request_is_anonymous_with_optional_ip_allow_list() -> None:
    open_port = build_port_request(SETTINGS)._to_dict()
    assert open_port == {"port": 8088, "auth": {"anonymous": True}}

    restricted = Settings(**{**SETTINGS.__dict__, "allowed_cidrs": ("203.0.113.7/32",)})
    acl = build_port_request(restricted)._to_dict()["ipAccessControl"]
    assert acl["defaultAction"] == "Deny"
    assert acl["rules"][0]["action"] == "Allow"
    assert acl["rules"][0]["sourceCidrs"] == ["203.0.113.7/32"]


# ---------- in-sandbox Responses server ----------


class FakeChatClient(BaseChatClient):
    STORES_BY_DEFAULT = False

    def _inner_get_response(
        self, *, messages: Sequence[Message], stream: bool, options: Mapping[str, object], **kwargs: object
    ) -> ResponseStream[ChatResponseUpdate, ChatResponse]:
        async def updates() -> AsyncIterator[ChatResponseUpdate]:
            yield ChatResponseUpdate(role="assistant", contents=[Content.from_text("Answer from the sandbox")])

        return ResponseStream(updates(), finalizer=ChatResponse.from_updates)


def test_responses_server_requires_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    created: list[Agent] = []

    def fake_agent(config: dict[str, Any], credential: Any) -> Agent:
        created.append(Agent(client=FakeChatClient(), name=sandbox_server.AGENT_NAME))
        return created[-1]

    monkeypatch.setattr(sandbox_server, "create_agent", fake_agent)
    server = sandbox_server.create_server({}, sandbox_server.FileTokenCredential(tmp_path / "t"), "k3y")
    body = {"input": "Hello", "stream": False, "store": False}

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://test") as client:
            assert (await client.get("/readiness")).status_code == 200
            assert (await client.post("/responses", json=body)).status_code == 401
            wrong = await client.post("/responses", json=body, headers={"Authorization": "Bearer nope"})
            assert wrong.status_code == 401
            for headers in ({"Authorization": "Bearer k3y"}, {"api-key": "k3y"}):
                response = await client.post("/responses", json=body, headers=headers)
                assert response.status_code == 200, response.text
                assert cli.output_text(response.json()) == "Answer from the sandbox"

    asyncio.run(scenario())
    assert len(created) == 2 and created[0] is not created[1]


def test_create_agent_uses_foundry_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: dict[str, Any] = {}
    monkeypatch.setattr(sandbox_server, "FoundryChatClient", lambda **kwargs: calls.setdefault("client", kwargs))
    monkeypatch.setattr(sandbox_server, "Agent", lambda **kwargs: calls.setdefault("agent", kwargs))
    credential = sandbox_server.FileTokenCredential(tmp_path / "t")

    sandbox_server.create_agent({"project_endpoint": "https://p", "model_deployment": "gpt-5"}, credential)

    assert calls["client"] == {"project_endpoint": "https://p", "model": "gpt-5", "credential": credential}
    assert calls["agent"]["client"] is calls["client"]


def test_file_token_credential_rereads_and_rejects_expired(tmp_path: Path) -> None:
    path = tmp_path / "token.json"
    credential = sandbox_server.FileTokenCredential(path)
    path.write_text(json.dumps({"token": "one", "expires_on": int(time.time()) + 600}))
    assert credential.get_token("scope").token == "one"
    path.write_text(json.dumps({"token": "two", "expires_on": int(time.time()) + 600}))
    assert credential.get_token("scope").token == "two"
    path.write_text(json.dumps({"token": "old", "expires_on": int(time.time()) - 1}))
    with pytest.raises(ClientAuthenticationError, match="refresh"):
        credential.get_token("scope")


def test_api_key_must_not_be_empty() -> None:
    with pytest.raises(ValueError):
        sandbox_server.ApiKeyMiddleware(object(), api_key="")


# ---------- host-side launcher ----------


@dataclass
class ExecResult:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class Port:
    port: int
    url: str | None


@dataclass
class FakeSandbox:
    fail_on: str | None = None
    ready_after: int = 0
    files: dict[str, str] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    ports: list[Any] = field(default_factory=list)
    deleted: bool = False
    resumed: bool = False
    sandbox_id: str = "sbx-123"

    async def mkdir(self, path: str) -> None:
        self.commands.append(f"mkdir {path}")

    async def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    async def exec(self, command: str, **kwargs: Any) -> ExecResult:
        self.commands.append(command)
        if self.fail_on and self.fail_on in command:
            return ExecResult(exit_code=1, stderr=f"{self.fail_on} failed")
        if "/readiness" in command:
            self.ready_after -= 1
            return ExecResult(exit_code=0 if self.ready_after < 0 else 1)
        if command.startswith("tail"):
            return ExecResult(stdout="server log line\n")
        return ExecResult()

    async def update_ports(self, ports: list[Any]) -> list[Port]:
        self.ports = ports
        return [Port(port=ports[0].port, url=PUBLIC_URL)]

    async def ensure_running(self) -> None:
        self.resumed = True

    async def delete(self) -> None:
        self.deleted = True


class FakePoller:
    def __init__(self, sandbox: FakeSandbox) -> None:
        self.sandbox = sandbox

    async def result(self) -> FakeSandbox:
        return self.sandbox


class FakeGroupClient:
    def __init__(self, sandbox: FakeSandbox) -> None:
        self.sandbox = sandbox
        self.create_kwargs: dict[str, Any] = {}
        self.deleted_ids: list[str] = []
        self.closed = False

    async def begin_create_sandbox(self, **kwargs: Any) -> FakePoller:
        self.create_kwargs = kwargs
        return FakePoller(self.sandbox)

    def get_sandbox_client(self, sandbox_id: str) -> FakeSandbox:
        assert sandbox_id == self.sandbox.sandbox_id
        return self.sandbox

    async def delete_sandbox(self, sandbox_id: str) -> None:
        self.deleted_ids.append(sandbox_id)

    async def close(self) -> None:
        self.closed = True


class FakeCredential:
    closed = False

    async def close(self) -> None:
        self.closed = True


def make_host(sandbox: FakeSandbox, **kwargs: Any) -> tuple[SandboxResponsesHost, FakeGroupClient, FakeCredential]:
    group, credential = FakeGroupClient(sandbox), FakeCredential()

    async def token_provider() -> tuple[str, int]:
        return TOKEN, 2_000_000_000

    host = SandboxResponsesHost(
        SETTINGS,
        token_provider=token_provider,
        client_factory=lambda settings: (group, credential),
        poll_interval=0,
        **kwargs,
    )
    return host, group, credential


def test_start_runs_server_in_sandbox_and_exposes_port() -> None:
    sandbox = FakeSandbox(ready_after=2)
    host, group, credential = make_host(sandbox)

    deployment = asyncio.run(host.start())

    assert deployment.sandbox_id == "sbx-123"
    assert deployment.url == PUBLIC_URL.rstrip("/")
    assert len(deployment.api_key) >= 32
    assert group.create_kwargs["auto_suspend_seconds"] == 3600
    assert "ResponsesHostServer" in sandbox.files["/workspace/sandbox_server.py"]
    assert "agent-framework-foundry-hosting" in sandbox.files["/workspace/requirements-sandbox.txt"]
    assert json.loads(sandbox.files["/workspace/server-config.json"]) == {
        "project_endpoint": SETTINGS.foundry_project_endpoint,
        "model_deployment": "gpt-5",
        "port": 8088,
    }
    assert TOKEN in sandbox.files["/workspace/.secrets/foundry-token.json"]
    assert sandbox.files["/workspace/.secrets/api-key"] == deployment.api_key
    assert all(TOKEN not in c and deployment.api_key not in c for c in sandbox.commands)
    assert any(c.startswith("nohup python3 /workspace/sandbox_server.py") for c in sandbox.commands)
    # The port is published only after the server is ready.
    assert sandbox.commands[-1].endswith("/readiness', timeout=5)\"")
    assert sandbox.ports[0].port == 8088
    assert not sandbox.deleted and group.closed and credential.closed


@pytest.mark.parametrize("fail_on", ["pip install", "nohup"])
def test_start_deletes_sandbox_on_failure(fail_on: str) -> None:
    sandbox = FakeSandbox(fail_on=fail_on)
    host, group, credential = make_host(sandbox)
    with pytest.raises(RuntimeError, match="failed"):
        asyncio.run(host.start())
    assert sandbox.deleted and group.closed and credential.closed


def test_start_reports_server_log_when_not_ready() -> None:
    sandbox = FakeSandbox(ready_after=10_000)
    host, _, _ = make_host(sandbox, readiness_timeout=0)
    with pytest.raises(RuntimeError, match="server log line"):
        asyncio.run(host.start())
    assert sandbox.deleted
    assert not sandbox.ports


def test_refresh_resumes_and_atomically_replaces_token() -> None:
    sandbox = FakeSandbox()
    host, group, _ = make_host(sandbox)

    assert asyncio.run(host.refresh("sbx-123")) == 2_000_000_000

    assert sandbox.resumed
    assert TOKEN in sandbox.files["/workspace/.secrets/foundry-token.json.new"]
    assert any("mv -f" in c and c.endswith("/workspace/.secrets/foundry-token.json") for c in sandbox.commands)
    assert all(TOKEN not in c for c in sandbox.commands)
    assert group.closed


def test_stop_deletes_sandbox() -> None:
    host, group, credential = make_host(FakeSandbox())
    asyncio.run(host.stop("sbx-123"))
    assert group.deleted_ids == ["sbx-123"] and group.closed and credential.closed


# ---------- CLI ----------


DEPLOYMENT = Deployment("sbx-123", "https://sbx-123.example", "the-key", 2_000_000_000)


def test_state_file_is_private_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    cli.save_state(DEPLOYMENT, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert cli.load_state(path) == DEPLOYMENT


def test_invoke_calls_responses_endpoint_with_bearer_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hi there"}]}],
            },
        )

    answer = asyncio.run(cli.invoke(DEPLOYMENT, "Hello", transport=httpx.MockTransport(handler)))

    assert answer == "Hi there"
    assert str(seen[0].url) == "https://sbx-123.example/responses"
    assert seen[0].headers["authorization"] == "Bearer the-key"
    assert json.loads(seen[0].content) == {"input": "Hello", "stream": False, "store": False}


def test_output_text_rejects_incomplete_responses() -> None:
    with pytest.raises(RuntimeError, match="failed"):
        cli.output_text({"status": "failed", "error": {"message": "boom"}})


def test_up_refuses_to_overwrite_existing_state(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.chdir(tmp_path)
    cli.save_state(DEPLOYMENT)
    with pytest.raises(RuntimeError, match="already exists"):
        asyncio.run(cli.main(["up"]))
