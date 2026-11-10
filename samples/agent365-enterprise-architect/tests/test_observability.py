# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Generator
from unittest.mock import AsyncMock

import jwt
import pytest
from azure.core.credentials import AccessToken
from opentelemetry import baggage
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import observability, sandbox_agent  # noqa: E402
from app.config import get_settings  # noqa: E402
from microsoft.opentelemetry.a365.core.exporters.span_processor import A365SpanProcessor  # noqa: E402
from microsoft.opentelemetry.a365.core.opentelemetry_scope import OpenTelemetryScope  # noqa: E402


@pytest.fixture
def tracing(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[InMemorySpanExporter, dict[str, Any]]]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(A365SpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    options: dict[str, Any] = {}
    monkeypatch.setattr(observability, "use_microsoft_opentelemetry", lambda **kwargs: options.update(kwargs))
    monkeypatch.setattr(observability.trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(OpenTelemetryScope, "_tracer", provider.get_tracer("test"))
    monkeypatch.setattr(OpenTelemetryScope, "_enabled_by_distro", True)
    monkeypatch.delenv("ENABLE_OBSERVABILITY", raising=False)
    monkeypatch.delenv("ENABLE_A365_OBSERVABILITY", raising=False)
    yield exporter, options
    provider.shutdown()


def make_telemetry(credential: Any, *, capture_content: bool = False) -> observability.SandboxObservability:
    return observability.SandboxObservability(
        credential,
        tenant_id="tenant",
        agent_id="agent",
        blueprint_id="blueprint",
        sponsor_id="sponsor",
        sponsor_name="Sponsor",
        sponsor_email="sponsor@example.com",
        capture_content=capture_content,
    )


@pytest.mark.parametrize("capture_content", [False, True])
def test_scopes_have_identity_caller_parentage_content_and_s2s_flags(
    tracing: tuple[InMemorySpanExporter, dict[str, Any]], capture_content: bool
) -> None:
    exporter, options = tracing
    credential = AsyncMock()
    credential.get_token.return_value = AccessToken("app-only-token", int(time.time()) + 3600)
    telemetry = make_telemetry(credential, capture_content=capture_content)

    async def exercise() -> None:
        await telemetry.start()
        from agent_framework.observability import OBSERVABILITY_SETTINGS
        assert not OBSERVABILITY_SETTINGS.enable_instrumentation
        try:
            assert telemetry.resolve_token("agent", "tenant") == "app-only-token"
            with telemetry.invocation("private prompt") as invocation:
                assert baggage.get_baggage("gen_ai.agent.id") == "agent"
                with telemetry.inference("private prompt", "gpt-5") as inference:
                    inference.record_input_tokens(4)
                    inference.record_output_tokens(8)
                    telemetry.record_output(inference, "private answer")
                telemetry.record_output(invocation, "private answer")
        finally:
            await telemetry.close()
        assert baggage.get_baggage("gen_ai.agent.id") is None

    asyncio.run(exercise())
    credential.get_token.assert_awaited_once_with(observability.OBSERVABILITY_SCOPE)
    assert options["enable_a365"] is True
    assert options["a365_enable_observability_exporter"] is True
    assert options["a365_use_s2s_endpoint"] is True
    assert options["a365_exporter_disable_offline_storage"] is True
    assert options["enable_sensitive_data"] is capture_content
    assert options["a365_suppress_invoke_agent_input"] is not capture_content
    assert options["instrumentation_options"]["agent_framework"]["enabled"] is False
    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    inference, invocation = spans
    assert inference.attributes["gen_ai.operation.name"] == "chat"
    assert invocation.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert inference.parent.span_id == invocation.context.span_id
    assert inference.attributes["gen_ai.usage.input_tokens"] == 4
    assert inference.attributes["gen_ai.usage.output_tokens"] == 8
    for span in spans:
        assert span.attributes["gen_ai.agent.id"] == "agent"
        assert span.attributes["microsoft.tenant.id"] == "tenant"
        assert span.attributes["microsoft.a365.agent.blueprint.id"] == "blueprint"
        assert span.attributes["user.id"] == "sponsor"
        assert span.attributes["user.email"] == "sponsor@example.com"
        assert "gen_ai.output.messages" in span.attributes
        serialized = json.dumps(dict(span.attributes))
        assert ("private prompt" in serialized) is capture_content
        assert ("private answer" in serialized) is capture_content


def test_resolver_rejects_unexpected_identity_and_expired_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    telemetry = make_telemetry(AsyncMock())
    with pytest.raises(RuntimeError, match="missing or expired"):
        telemetry.resolve_token("agent", "tenant")
    telemetry._token = AccessToken("token", 2000)
    monkeypatch.setattr(observability.time, "time", lambda: 1000)
    assert telemetry.resolve_token("agent", "tenant") == "token"
    for agent, tenant in [("blueprint", "tenant"), ("agent", "another-tenant")]:
        with pytest.raises(ValueError, match="unexpected agent or tenant"):
            telemetry.resolve_token(agent, tenant)
    monkeypatch.setattr(observability.time, "time", lambda: 2000)
    with pytest.raises(RuntimeError, match="expired"):
        telemetry.resolve_token("agent", "tenant")


def test_blueprint_cannot_be_used_as_exporting_identity() -> None:
    with pytest.raises(ValueError, match="not its blueprint"):
        observability.SandboxObservability(
            AsyncMock(),
            tenant_id="tenant",
            agent_id="blueprint",
            blueprint_id="blueprint",
            sponsor_id="sponsor",
            sponsor_name="Sponsor",
            sponsor_email="sponsor@example.com",
        )


def test_token_failure_is_not_silently_ignored() -> None:
    credential = AsyncMock()
    credential.get_token.side_effect = RuntimeError("MI unavailable")
    with pytest.raises(RuntimeError, match="MI unavailable"):
        asyncio.run(make_telemetry(credential).start())


def test_initial_expired_token_is_reported() -> None:
    credential = AsyncMock()
    credential.get_token.return_value = AccessToken("expired", int(time.time()) - 1)
    with pytest.raises(RuntimeError, match="already expired"):
        asyncio.run(make_telemetry(credential).start())


def test_flush_timeout_is_reported_and_provider_is_shutdown(
    monkeypatch: pytest.MonkeyPatch, tracing: tuple[InMemorySpanExporter, dict[str, Any]]
) -> None:
    credential = AsyncMock()
    credential.get_token.return_value = AccessToken("token", int(time.time()) + 3600)
    telemetry = make_telemetry(credential)
    provider = observability.trace.get_tracer_provider()
    monkeypatch.setattr(provider, "force_flush", lambda timeout: False)
    shutdown_calls: list[bool] = []
    monkeypatch.setattr(provider, "shutdown", lambda: shutdown_calls.append(True))

    async def exercise() -> None:
        await telemetry.start()
        await telemetry.close()

    with pytest.raises(RuntimeError, match="Timed out flushing"):
        asyncio.run(exercise())
    assert shutdown_calls == [True]


def test_refresh_loop_renews_token_before_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = AsyncMock()
    credential.get_token.return_value = AccessToken("new-token", 7200)
    telemetry = make_telemetry(credential)
    telemetry._token = AccessToken("old-token", 3600)
    monkeypatch.setattr(observability.time, "time", lambda: 1000)
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    monkeypatch.setattr(observability.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(telemetry._refresh_loop())
    assert sleep.await_args_list[0].args == (2300,)
    credential.get_token.assert_awaited_once_with(observability.OBSERVABILITY_SCOPE)
    assert telemetry.resolve_token("agent", "tenant") == "new-token"


@pytest.mark.parametrize("fail_model", [False, True])
def test_runtime_records_model_response_and_flushes_on_success_or_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tracing: tuple[InMemorySpanExporter, dict[str, Any]],
    fail_model: bool,
) -> None:
    token = jwt.encode(
        {"appid": "agent", "xms_par_app_azp": "blueprint"},
        "test-signing-key-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )
    acquire = AsyncMock(return_value=AccessToken(token, int(time.time()) + 3600))
    close = AsyncMock()
    monkeypatch.setattr(sandbox_agent.AgentIdentityCredential, "get_token", acquire)
    monkeypatch.setattr(sandbox_agent.AgentIdentityCredential, "close", close)
    def invoke(request: dict[str, Any], access_token: AccessToken) -> str:
        assert access_token.token == token
        if fail_model:
            raise RuntimeError("model unavailable")
        return "answer"

    monkeypatch.setattr(sandbox_agent, "invoke_prompt_agent", invoke)
    path = tmp_path / "request.json"
    path.write_text(json.dumps({
        "tenant_id": "tenant", "blueprint_app_id": "blueprint", "agent_identity_client_id": "agent",
        "project_endpoint": "https://example.test", "model_deployment": "gpt-5", "question": "hello",
        "agent_name": "architect", "agent_version": "3",
        "observability_enabled": True, "observability_sponsor_id": "sponsor",
        "observability_sponsor_name": "Sponsor", "observability_sponsor_email": "sponsor@example.com",
    }))
    if fail_model:
        with pytest.raises(RuntimeError, match="model unavailable"):
            asyncio.run(sandbox_agent.run(path))
    else:
        assert asyncio.run(sandbox_agent.run(path))["answer"] == "answer"
    close.assert_awaited_once()
    assert [call.args for call in acquire.await_args_list] == [
        (sandbox_agent.FOUNDRY_SCOPE,), (observability.OBSERVABILITY_SCOPE,)
    ]
    spans = tracing[0].get_finished_spans()
    assert len(spans) == 2
    if fail_model:
        assert all(span.status.status_code == StatusCode.ERROR for span in spans)


@pytest.fixture
def launcher_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AZURE_TENANT_ID", "AGENT_BLUEPRINT_APP_ID", "AGENT_IDENTITY_CLIENT_ID", "FOUNDRY_PROJECT_ENDPOINT",
        "AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZURE_SANDBOX_GROUP", "AZURE_SANDBOX_REGION",
    ):
        monkeypatch.setenv(name, "configured")
    monkeypatch.delenv("ENABLE_A365_OBSERVABILITY_EXPORTER", raising=False)
    monkeypatch.delenv("A365_CAPTURE_CONTENT", raising=False)
    for name in ("A365_SPONSOR_ID", "A365_SPONSOR_NAME", "A365_SPONSOR_EMAIL"):
        monkeypatch.delenv(name, raising=False)


def test_observability_defaults_to_disabled_and_redacted(launcher_env: None) -> None:
    settings = get_settings()
    assert not settings.observability_enabled
    assert not settings.observability_capture_content


def test_enabled_observability_requires_real_sponsor(launcher_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_A365_OBSERVABILITY_EXPORTER", "true")
    with pytest.raises(RuntimeError, match="A365_SPONSOR_ID"):
        get_settings()
    monkeypatch.setenv("A365_SPONSOR_ID", "sponsor")
    monkeypatch.setenv("A365_SPONSOR_NAME", "Sponsor")
    monkeypatch.setenv("A365_SPONSOR_EMAIL", "sponsor@example.com")
    settings = get_settings()
    assert settings.observability_enabled
    assert settings.observability_sponsor_id == "sponsor"
    assert not settings.observability_capture_content


def test_invalid_observability_flag_is_reported(launcher_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_A365_OBSERVABILITY_EXPORTER", "maybe")
    with pytest.raises(RuntimeError, match="must be true or false"):
        get_settings()


def test_real_distro_exports_two_scopes_to_s2s_route_in_isolated_process() -> None:
    code = """
import asyncio
import json
import time
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import requests
from azure.core.credentials import AccessToken
from app.observability import SandboxObservability

sends = []

def send(self, method, url, **kwargs):
    assert method.upper() == "POST"
    assert urlparse(url).hostname == "agent365.svc.cloud.microsoft"
    assert "/observabilityService/" in url
    assert kwargs["headers"]["authorization"] == "Bearer synthetic-app-token"
    sends.append(json.loads(kwargs["data"]))
    response = requests.Response()
    response.status_code = 200
    response._content = b"{}"
    return response

requests.Session.request = send

async def main():
    credential = AsyncMock()
    credential.get_token.return_value = AccessToken("synthetic-app-token", int(time.time()) + 3600)
    telemetry = SandboxObservability(
        credential, tenant_id="11111111-1111-1111-1111-111111111111",
        agent_id="22222222-2222-2222-2222-222222222222",
        blueprint_id="33333333-3333-3333-3333-333333333333",
        sponsor_id="44444444-4444-4444-4444-444444444444",
        sponsor_name="Synthetic sponsor", sponsor_email="sponsor@example.com",
    )
    await telemetry.start()
    try:
        with telemetry.invocation("private prompt") as invocation:
            with telemetry.inference("private prompt", "gpt-5") as inference:
                telemetry.record_output(inference, "private answer")
            telemetry.record_output(invocation, "private answer")
    finally:
        await telemetry.close()
    assert sends, "No HTTP export occurred"
    spans = [
        span for payload in sends for resource in payload["resourceSpans"]
        for scope in resource["scopeSpans"] for span in scope["spans"]
    ]
    assert len(spans) == 2, spans
    assert "private prompt" not in json.dumps(sends)
    assert "private answer" not in json.dumps(sends)
    print("Verified real distro: 2 redacted spans exported over S2S")

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 redacted spans exported over S2S" in result.stdout
