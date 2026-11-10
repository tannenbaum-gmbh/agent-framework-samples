# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import IdentitySettings, Settings  # noqa: E402
from app.host import AgentIdentitySandboxAgent  # noqa: E402
from app.sandbox_agent import describe_identity  # noqa: E402

SETTINGS = Settings(
    foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
    foundry_model_deployment="gpt-5",
    subscription_id="sub",
    resource_group="rg",
    sandbox_group="group",
    sandbox_region="westus2",
    sandbox_disk_image="python-3.14",
    identity=IdentitySettings(
        tenant_id="tenant",
        blueprint_app_id="blueprint-app",
        agent_identity_client_id="agent-app",
        blueprint_client_secret="secret",
        managed_identity_client_id="managed-identity-client",
    ),
)

SANDBOX_OUTPUT = json.dumps(
    {"answer": "Answer from sandbox", "agent_identity": "agent-app", "blueprint": "blueprint-app", "user_agent": "UA"}
)


@dataclass
class ExecResult:
    exit_code: int = 0
    stdout: str = ""
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
        return ExecResult(stdout=f"some log line\n{SANDBOX_OUTPUT}\n" if "sandbox_agent.py" in command else "")

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


def test_agent_runs_in_sandbox_as_agent_identity_without_secrets_in_commands() -> None:
    sandbox = FakeSandbox()
    client = Closeable(sandbox)
    credential = Closeable()

    agent = AgentIdentitySandboxAgent(SETTINGS, client_factory=lambda settings: (client, credential))
    result = asyncio.run(
        agent.run("question with 'shell syntax' $(oops)", agent_name="architect", version="4")
    )

    assert result.answer == "Answer from sandbox"
    assert result.agent_identity == "agent-app"
    assert result.blueprint == "blueprint-app"
    request = json.loads(sandbox.files["/workspace/request.json"])
    assert request["agent_identity_client_id"] == "agent-app"
    assert "shell syntax" in request["question"]
    assert request["tenant_id"] == "tenant"
    assert request["blueprint_app_id"] == "blueprint-app"
    assert request["managed_identity_client_id"] == "managed-identity-client"
    assert request["agent_name"] == "architect"
    assert request["agent_version"] == "4"
    assert set(sandbox.files) == {
        "/workspace/sandbox_agent.py", "/workspace/agent_token.py",
        "/workspace/requirements-sandbox.txt", "/workspace/request.json"
    }
    assert "secret" not in request
    assert SETTINGS.identity.blueprint_client_secret not in request.values()
    assert "microsoft-agents-a365-runtime" in sandbox.files["/workspace/requirements-sandbox.txt"]
    assert all(
        "--token" not in command and "shell syntax" not in command for command in sandbox.commands
    )
    assert client.create_calls[0]["labels"]["agent-identity"] == "agent-app"
    assert sandbox.deleted
    assert client.closed
    assert credential.closed


def test_system_assigned_identity_does_not_send_client_id() -> None:
    sandbox = FakeSandbox()
    client, credential = Closeable(sandbox), Closeable()
    settings = replace(SETTINGS, identity=replace(SETTINGS.identity, managed_identity_client_id=None))
    agent = AgentIdentitySandboxAgent(settings, client_factory=lambda settings: (client, credential))
    asyncio.run(agent.run("question", agent_name="architect", version="1"))
    assert json.loads(sandbox.files["/workspace/request.json"])["managed_identity_client_id"] is None
    assert sandbox.deleted and client.closed and credential.closed


def test_observability_uploads_code_and_sponsor_without_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class LoggingSandbox(FakeSandbox):
        async def exec(self, command: str, **kwargs: Any) -> ExecResult:
            result = await super().exec(command, **kwargs)
            if command.startswith("python3 /workspace/sandbox_agent.py"):
                result.stderr = "HTTP 200 - spans exported\n"
            return result

    monkeypatch.setenv("OTEL_LOG_LEVEL", "WARN")
    monkeypatch.setenv("A365_OBSERVABILITY_LOG_LEVEL", "warn|error")
    settings = replace(
        SETTINGS,
        observability_enabled=True,
        observability_capture_content=True,
        observability_sponsor_id="sponsor",
        observability_sponsor_name="Sponsor",
        observability_sponsor_email="sponsor@example.com",
    )
    sandbox = LoggingSandbox()
    client, credential = Closeable(sandbox), Closeable()
    agent = AgentIdentitySandboxAgent(settings, client_factory=lambda settings: (client, credential))
    asyncio.run(agent.run("question", agent_name="architect", version="1"))
    request = json.loads(sandbox.files["/workspace/request.json"])
    assert request["observability_enabled"] is True
    assert request["observability_capture_content"] is True
    assert request["observability_sponsor_id"] == "sponsor"
    assert request["observability_sponsor_name"] == "Sponsor"
    assert request["observability_sponsor_email"] == "sponsor@example.com"
    assert request["OTEL_LOG_LEVEL"] == "WARN"
    assert request["A365_OBSERVABILITY_LOG_LEVEL"] == "warn|error"
    assert SETTINGS.identity.blueprint_client_secret not in request.values()
    assert "/workspace/observability.py" in sandbox.files
    assert "HTTP 200" in capsys.readouterr().err
    assert sandbox.deleted and client.closed and credential.closed


def test_sandbox_token_failure_is_reported_and_compute_is_cleaned_up() -> None:
    class FailingSandbox(FakeSandbox):
        async def exec(self, command: str, **kwargs: Any) -> ExecResult:
            if command.startswith("python3 /workspace/sandbox_agent.py"):
                return ExecResult(exit_code=1, stderr="Agent identity token request failed: disabled")
            return await super().exec(command, **kwargs)

    sandbox = FailingSandbox()
    client, credential = Closeable(sandbox), Closeable()
    agent = AgentIdentitySandboxAgent(SETTINGS, client_factory=lambda settings: (client, credential))
    with pytest.raises(RuntimeError, match="disabled"):
        asyncio.run(agent.run("question", agent_name="architect", version="1"))
    assert sandbox.deleted and client.closed and credential.closed


def test_obo_caller_token_is_only_in_protected_request_and_cleanup_runs() -> None:
    sandbox = FakeSandbox()
    client, credential = Closeable(sandbox), Closeable()
    settings = replace(SETTINGS, auth_mode="obo")
    agent = AgentIdentitySandboxAgent(settings, client_factory=lambda settings: (client, credential))
    asyncio.run(agent.run(
        "question", agent_name="architect", version="1", user_assertion="caller-token",
        channel_name="msteams", conversation_id="conversation-7",
    ))
    request = json.loads(sandbox.files["/workspace/request.json"])
    assert request["auth_mode"] == "obo"
    assert request["user_assertion"] == "caller-token"
    assert request["channel_name"] == "msteams"
    assert request["conversation_id"] == "conversation-7"
    assert all("caller-token" not in command for command in sandbox.commands)
    assert sandbox.commands[0].startswith("python3 -m pip install")
    assert sandbox.commands[1].startswith("umask 077; touch /workspace/request.json; chmod 600")
    assert "rm -f /workspace/request.json" in sandbox.commands
    assert sandbox.deleted and client.closed and credential.closed


@pytest.mark.parametrize(
    ("mode", "token"),
    [("obo", None), ("obo", " "), ("s2s", "caller-token"), ("invalid", None)],
)
def test_invalid_authentication_input_fails_before_creating_sandbox(mode: str, token: str | None) -> None:
    def unexpected_client(settings: Settings) -> Any:
        pytest.fail("No sandbox should be created")

    agent = AgentIdentitySandboxAgent(replace(SETTINGS, auth_mode=mode), client_factory=unexpected_client)
    with pytest.raises(ValueError):
        asyncio.run(agent.run("question", agent_name="architect", version="1", user_assertion=token))


def test_request_permission_failure_prevents_token_upload_and_deletes_sandbox() -> None:
    class PermissionDeniedSandbox(FakeSandbox):
        async def exec(self, command: str, **kwargs: Any) -> ExecResult:
            if command.startswith("umask"):
                return ExecResult(exit_code=1, stderr="permission denied")
            return await super().exec(command, **kwargs)

    sandbox = PermissionDeniedSandbox()
    client, credential = Closeable(sandbox), Closeable()
    agent = AgentIdentitySandboxAgent(
        replace(SETTINGS, auth_mode="obo"), client_factory=lambda settings: (client, credential)
    )
    with pytest.raises(RuntimeError, match="Unable to protect sandbox request file"):
        asyncio.run(agent.run("question", agent_name="architect", version="1", user_assertion="caller-token"))
    assert "/workspace/request.json" not in sandbox.files
    assert sandbox.deleted and client.closed and credential.closed


def _token(claims: dict[str, str]) -> str:
    return jwt.encode(claims, "test-signing-key-that-is-long-enough-for-hs256", algorithm="HS256")


def test_describe_identity_uses_agent365_sdk_claims() -> None:
    identity = describe_identity(_token({"appid": "agent-app", "xms_par_app_azp": "blueprint-app"}), "agent-app")
    assert identity["agent_identity"] == "agent-app"
    assert identity["blueprint"] == "blueprint-app"
    assert identity["user_agent"].startswith("Agent365SDK/")


def test_describe_identity_rejects_wrong_subject() -> None:
    with pytest.raises(RuntimeError, match="expected agent identity agent-app"):
        describe_identity(_token({"appid": "developer-cli"}), "agent-app")
