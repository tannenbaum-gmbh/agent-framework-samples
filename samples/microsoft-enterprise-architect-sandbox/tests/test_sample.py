from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import httpx2
import pytest
from azure.core.credentials import AccessToken
from openai import AsyncOpenAI

from app import agent, host, invoke, service
from app.config import RuntimeSettings, Settings, get_settings
from app.tokens import FOUNDRY_SCOPE, WORKIQ_SCOPE, FileTokenCredential, read_token

RUNTIME = RuntimeSettings(
    "https://example.services.ai.azure.com/api/projects/demo", "workshop-model", "k" * 40,
)
SETTINGS = Settings(RUNTIME, "subscription", "rg", "existing-group", "eastus", "tenant", "app")


@pytest.fixture(autouse=True)
def private_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("HOME", str(tmp_path))


def write_tokens(path: Path, *, expires_on=None, suffix=""):
    path.write_text(json.dumps({
        name: {"token": name + suffix, "expires_on": expires_on or int(time.time()) + 3600}
        for name in ("foundry", "workiq")
    }), encoding="utf-8")


def test_settings_require_configuration_and_reject_weak_key(monkeypatch):
    for name in (
        "FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL_DEPLOYMENT", "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        "SERVICE_API_KEY", "AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZURE_SANDBOX_GROUP",
        "AZURE_SANDBOX_REGION", "AZURE_TENANT_ID", "WORKIQ_CLIENT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="FOUNDRY_PROJECT_ENDPOINT"):
        get_settings()
    for name, value in {
        "FOUNDRY_PROJECT_ENDPOINT": RUNTIME.project_endpoint,
        "SERVICE_API_KEY": RUNTIME.api_key,
        "AZURE_SUBSCRIPTION_ID": "subscription",
        "AZURE_RESOURCE_GROUP": "rg",
        "AZURE_SANDBOX_GROUP": "existing-group",
        "AZURE_SANDBOX_REGION": "eastus",
        "AZURE_TENANT_ID": "tenant",
        "WORKIQ_CLIENT_ID": "app",
    }.items():
        monkeypatch.setenv(name, value)
    assert get_settings().runtime.model_deployment == "gpt-5.3-codex-2"
    monkeypatch.setenv("FOUNDRY_MODEL_DEPLOYMENT", " custom ")
    assert get_settings().runtime.model_deployment == "custom"
    with pytest.raises(ValueError, match="SERVICE_API_KEY"):
        replace(RUNTIME, api_key="short")
    with pytest.raises(ValueError, match="HTTPS"):
        replace(RUNTIME, project_endpoint="http://example.test")


def test_token_expiry_audience_and_refresh(tmp_path):
    path = tmp_path / "tokens.json"
    write_tokens(path)
    credential = FileTokenCredential(path)
    assert credential.get_token(FOUNDRY_SCOPE).token == "foundry"
    write_tokens(path, suffix="-new")
    assert credential.get_token(FOUNDRY_SCOPE).token == "foundry-new"
    with pytest.raises(ValueError, match="audience"):
        credential.get_token(WORKIQ_SCOPE)
    write_tokens(path, expires_on=int(time.time()) + 30)
    with pytest.raises(RuntimeError, match="expiring"):
        read_token(path, "workiq")


def test_agent_wires_direct_catalogs_and_dynamic_workiq_headers(monkeypatch, tmp_path):
    path = tmp_path / "tokens.json"
    write_tokens(path)
    tools = MagicMock()
    model = MagicMock()
    factory = MagicMock()
    monkeypatch.setattr(agent, "MCPStreamableHTTPTool", tools)
    monkeypatch.setattr(agent, "FoundryChatClient", model)
    monkeypatch.setattr(agent, "Agent", factory)
    agent.create_agent(RUNTIME, path)
    options = factory.call_args.kwargs
    assert options["default_options"] == {"store": False}
    assert "Mermaid" in options["instructions"]
    assert "never overwrite" in options["instructions"]
    assert "default_headers" not in model.call_args.kwargs
    learn, workiq = [call.kwargs for call in tools.call_args_list]
    assert learn["url"] == agent.MSLEARN_MCP_URL
    assert workiq["url"] == agent.WORKIQ_MCP_URL
    for tool in (learn, workiq):
        assert tool["approval_mode"] == "never_require"
        assert "allowed_tools" not in tool
        assert "project_connection_id" not in tool
    assert "header_provider" not in learn
    assert workiq["header_provider"]({}) == {"Authorization": "Bearer workiq"}
    write_tokens(path, suffix="-refreshed")
    assert workiq["header_provider"]({}) == {"Authorization": "Bearer workiq-refreshed"}


@pytest.mark.parametrize("route", ["/readiness", "/responses", "/responses/private", "/_before_snapshot"])
async def test_auth_protects_every_route_without_creating_agent(monkeypatch, tmp_path, route):
    create = MagicMock()
    monkeypatch.setattr(service, "create_agent", create)
    server = service.create_server(RUNTIME, tmp_path / "tokens.json")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server), base_url="http://test",
    ) as client:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {
            "Authorization": "Bearer " + RUNTIME.api_key + "wrong",
        }):
            response = await client.post(route, headers=headers, json={"input": "hello"})
            assert response.status_code == 401
            assert response.headers["WWW-Authenticate"] == "Bearer"
        duplicate = await client.get(route, headers=[
            ("Authorization", f"Bearer {RUNTIME.api_key}"),
            ("Authorization", f"Bearer {RUNTIME.api_key}"),
        ])
        assert duplicate.status_code == 401
    create.assert_not_called()


async def test_real_responses_http_with_model_and_mcp_transports(monkeypatch, tmp_path):
    token_path = tmp_path / "tokens.json"
    write_tokens(token_path)
    model_requests = []
    mcp_requests = []
    clients = []
    real_model = agent.FoundryChatClient
    real_tool = agent.MCPStreamableHTTPTool

    def model_transport(request):
        model_requests.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == RUNTIME.model_deployment
        assert payload["store"] is False
        assert not any(key.startswith("x-agent-foundry") for key in request.headers)
        assert {tool["name"] for tool in payload["tools"]} == {"MicrosoftLearn_lookup", "WorkIQ_lookup"}
        message = {
            "id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "Architecture", "annotations": []}],
        }
        events = [
            {"type": "response.output_text.delta", "sequence_number": 0, "item_id": "msg_test",
             "output_index": 0, "content_index": 0, "delta": "Architecture"},
            {"type": "response.output_item.done", "sequence_number": 1, "output_index": 0, "item": message},
            {"type": "response.completed", "sequence_number": 2, "response": {
                "id": "resp_model", "object": "response", "created_at": 0, "status": "completed",
                "model": RUNTIME.model_deployment, "output": [message],
            }},
        ]
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"},
            text="".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n",
        )

    def mcp_transport(request):
        mcp_requests.append(request)
        if request.method != "POST":
            return httpx2.Response(405)
        payload = json.loads(request.content)
        if "id" not in payload:
            return httpx2.Response(202)
        if payload["method"] == "server/discover":
            return httpx2.Response(200, json={
                "jsonrpc": "2.0", "id": payload["id"],
                "error": {"code": -32601, "message": "Method not found"},
            })
        if payload["method"] == "ping":
            result = {}
        elif payload["method"] == "initialize":
            result = {
                "protocolVersion": payload["params"]["protocolVersion"],
                "capabilities": {"tools": {}}, "serverInfo": {"name": "offline", "version": "1"},
            }
        elif payload["method"] == "tools/list":
            result = {"tools": [{
                "name": "lookup", "description": "Look up evidence",
                "inputSchema": {"type": "object", "properties": {}},
            }]}
        else:
            raise AssertionError(payload)
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    def create_model(**kwargs):
        http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(model_transport))
        clients.append(http_client)
        project = MagicMock()
        project.get_openai_client.side_effect = lambda **options: AsyncOpenAI(
            api_key="offline-model-token", base_url=f"{RUNTIME.project_endpoint}/openai/v1",
            http_client=http_client, **options,
        )
        return real_model(project_client=project, **kwargs)

    def create_tool(**kwargs):
        http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(mcp_transport))
        clients.append(http_client)
        return real_tool(http_client=http_client, **kwargs)

    monkeypatch.setattr(agent, "FoundryChatClient", create_model)
    monkeypatch.setattr(agent, "MCPStreamableHTTPTool", create_tool)
    server = service.create_server(RUNTIME, token_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server), base_url="http://test",
            headers={"Authorization": f"Bearer {RUNTIME.api_key}"},
        ) as client:
            assert (await client.get("/readiness")).status_code == 200
            for stream in (False, True):
                response = await client.post("/responses", json={
                    "input": "Design an architecture", "store": False, "stream": stream,
                }, headers={"x-agent-foundry-call-id": "do-not-forward"})
                assert response.status_code == 200, response.text
                if not stream:
                    assert response.json()["status"] == "completed", response.text
                assert "Architecture" in response.text
                write_tokens(token_path, suffix="-refreshed")
        assert len(model_requests) == 2
        assert mcp_requests
        for request in mcp_requests:
            if request.url.host == "workiq.svc.cloud.microsoft":
                assert request.headers["authorization"] in ("Bearer workiq", "Bearer workiq-refreshed")
            else:
                assert "authorization" not in request.headers
        assert {
            request.headers.get("authorization") for request in mcp_requests
            if request.url.host == "workiq.svc.cloud.microsoft"
        } == {"Bearer workiq", "Bearer workiq-refreshed"}
    finally:
        for client in clients:
            await client.aclose()


def fake_sandbox():
    sandbox = SimpleNamespace(
        write_file=AsyncMock(), exec=AsyncMock(return_value=SimpleNamespace(exit_code=0, stderr="")),
        add_port=AsyncMock(return_value=SimpleNamespace(url="https://sandbox.example")),
        delete=AsyncMock(), close=AsyncMock(),
    )
    group = SimpleNamespace(begin_create_sandbox=AsyncMock(
        return_value=SimpleNamespace(result=AsyncMock(return_value=sandbox)),
    ))
    token = SimpleNamespace(get_token=AsyncMock(
        return_value=AccessToken("short-lived", int(time.time()) + 3600),
    ))
    return sandbox, group, token


async def test_supervisor_uploads_only_runtime_and_deletes_own_sandbox(monkeypatch, capsys):
    sandbox, group, token = fake_sandbox()
    ready = AsyncMock()
    monkeypatch.setattr(host, "wait_ready", ready)
    stop = asyncio.Event()
    stop.set()
    await host.serve(SETTINGS, group, token, token, stop)
    options = group.begin_create_sandbox.call_args.kwargs
    assert options["disk"] == "python-3.14"
    assert options["auto_suspend_seconds"] == 3600
    sandbox.add_port.assert_awaited_once_with(8080, anonymous=True)
    names = [call.args[0] for call in sandbox.write_file.call_args_list]
    assert not any(name.endswith((".env", "host.py")) for name in names)
    assert {name.removeprefix(host.WORKSPACE + "/") for name in names} == {
        *host.RUNTIME_FILES, "config.json", "tokens.json.new",
    }
    for call in sandbox.write_file.call_args_list:
        if call.args[0].endswith(("config.json", "tokens.json.new")):
            assert call.kwargs["mode"] == "0600"
    ready.assert_awaited_once_with("https://sandbox.example", RUNTIME.api_key)
    sandbox.delete.assert_awaited_once()
    sandbox.close.assert_awaited_once()
    output = capsys.readouterr().out
    assert "https://sandbox.example" in output
    assert RUNTIME.api_key not in output


@pytest.mark.parametrize("failure", ["upload", "install", "port", "readiness", "refresh"])
async def test_supervisor_cleans_up_on_failures(monkeypatch, failure):
    sandbox, group, token = fake_sandbox()
    monkeypatch.setattr(host, "wait_ready", AsyncMock())
    if failure == "upload":
        sandbox.write_file.side_effect = RuntimeError("upload failed")
    elif failure == "install":
        sandbox.exec.side_effect = [
            SimpleNamespace(exit_code=0), SimpleNamespace(exit_code=1, stderr="install failed"),
        ]
    elif failure == "port":
        sandbox.add_port.return_value.url = None
    elif failure == "readiness":
        monkeypatch.setattr(host, "wait_ready", AsyncMock(side_effect=RuntimeError("not ready")))
    else:
        refresh = AsyncMock(side_effect=[None, None, RuntimeError("refresh failed")])
        monkeypatch.setattr(host, "upload_tokens", refresh)
        async def timeout(*args, **kwargs):
            args[0].close()
            raise TimeoutError
        monkeypatch.setattr(host.asyncio, "wait_for", timeout)
    with pytest.raises(RuntimeError):
        await host.serve(SETTINGS, group, token, token, asyncio.Event())
    sandbox.delete.assert_awaited_once()
    sandbox.close.assert_awaited_once()


async def test_atomic_refresh_checks_command_failure():
    sandbox, _, token = fake_sandbox()
    sandbox.exec.return_value.exit_code = 1
    with pytest.raises(RuntimeError, match="atomically"):
        await host.upload_tokens(sandbox, token, token)


async def test_supervisor_refreshes_and_stops_cleanly(monkeypatch):
    sandbox, group, token = fake_sandbox()
    stop = asyncio.Event()
    ready = AsyncMock()
    monkeypatch.setattr(host, "wait_ready", ready)
    refresh = AsyncMock()
    monkeypatch.setattr(host, "upload_tokens", refresh)

    async def timeout_then_stop(awaitable, **kwargs):
        awaitable.close()
        stop.set()
        raise asyncio.TimeoutError

    monkeypatch.setattr(host.asyncio, "wait_for", timeout_then_stop)
    await host.serve(SETTINGS, group, token, token, stop)
    assert refresh.await_count == 3
    assert ready.await_count == 2
    sandbox.delete.assert_awaited_once()


async def test_device_code_token_source_keeps_sync_auth_off_event_loop():
    credential = MagicMock()
    expected = AccessToken("delegated", int(time.time()) + 3600)
    credential.get_token.return_value = expected
    result = await host.DelegatedTokenSource(credential).get_token(WORKIQ_SCOPE)
    assert result == expected
    credential.get_token.assert_called_once_with(WORKIQ_SCOPE)


async def test_readiness_retries_transient_failures_but_rejects_bad_auth(monkeypatch):
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.side_effect = [
        httpx.ConnectError("starting"),
        httpx.Response(503, request=httpx.Request("GET", "https://test/readiness")),
        httpx.Response(200),
    ]
    monkeypatch.setattr(host.httpx, "AsyncClient", MagicMock(return_value=client))
    monkeypatch.setattr(host.asyncio, "sleep", AsyncMock())
    await host.wait_ready("https://test", RUNTIME.api_key)
    assert client.get.await_count == 3
    client.get.side_effect = None
    client.get.return_value = httpx.Response(401, request=httpx.Request("GET", "https://test/readiness"))
    with pytest.raises(httpx.HTTPStatusError):
        await host.wait_ready("https://test", RUNTIME.api_key)
    client.get.return_value = httpx.Response(503, request=httpx.Request("GET", "https://test/readiness"))
    with pytest.raises(RuntimeError, match="two minutes"):
        await host.wait_ready("https://test", RUNTIME.api_key)


async def test_readiness_has_two_minute_overall_deadline(monkeypatch):
    wait = AsyncMock(side_effect=asyncio.TimeoutError)
    probe = MagicMock(return_value=object())
    monkeypatch.setattr(host, "probe_readiness", probe)
    monkeypatch.setattr(host.asyncio, "wait_for", wait)
    with pytest.raises(RuntimeError, match="two minutes"):
        await host.wait_ready("https://test", RUNTIME.api_key)
    wait.assert_awaited_once_with(probe.return_value, timeout=120)


def test_service_entrypoint_binds_public_interface(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "project_endpoint": RUNTIME.project_endpoint,
        "model_deployment": RUNTIME.model_deployment,
        "api_key": RUNTIME.api_key,
    }))
    server = MagicMock()
    factory = MagicMock(return_value=server)
    monkeypatch.setattr(service, "create_server", factory)
    monkeypatch.setattr("sys.argv", [
        "service", "--config", str(config), "--tokens", str(tmp_path / "tokens.json"),
    ])
    service.main()
    factory.assert_called_once_with(RUNTIME, tmp_path / "tokens.json")
    server.run.assert_called_once_with(host="0.0.0.0", port=8080)


def test_invoker_uses_sandbox_root_and_disables_mutation_retries(monkeypatch, capsys):
    client = MagicMock()
    client.__enter__.return_value = client
    client.responses.create.return_value = SimpleNamespace(
        id="resp_test", status="completed", output_text="Architecture",
    )
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(invoke, "OpenAI", factory)
    monkeypatch.setenv("SERVICE_URL", "https://sandbox.example/")
    monkeypatch.setenv("SERVICE_API_KEY", RUNTIME.api_key)
    monkeypatch.setattr("sys.argv", ["invoke", "question"])
    invoke.main()
    assert factory.call_args.kwargs["base_url"] == "https://sandbox.example/"
    assert factory.call_args.kwargs["max_retries"] == 0
    client.responses.create.assert_called_once_with(
        model="mea-sandbox-agent", input="question", store=False,
    )
    assert capsys.readouterr().out.strip() == "Architecture"


@pytest.mark.parametrize("status,text", [("failed", ""), ("incomplete", ""), ("completed", "")])
def test_invoker_does_not_claim_success_for_failure_or_empty_output(monkeypatch, status, text, capsys):
    client = MagicMock()
    client.__enter__.return_value = client
    client.responses.create.return_value = SimpleNamespace(
        id="resp_test", status=status, output_text=text, error="failure",
    )
    monkeypatch.setattr(invoke, "OpenAI", MagicMock(return_value=client))
    monkeypatch.setenv("SERVICE_URL", "https://sandbox.example")
    monkeypatch.setenv("SERVICE_API_KEY", RUNTIME.api_key)
    monkeypatch.setattr("sys.argv", ["invoke", "question"])
    with pytest.raises(RuntimeError):
        invoke.main()
    assert not capsys.readouterr().out
