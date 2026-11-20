from __future__ import annotations

import asyncio
import json
import runpy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from zipfile import ZipFile

import httpx
import httpx2
import pytest
from agent_framework import ChatContext, ChatResponse
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.projects.models import AgentEndpointConfig
from openai import AsyncOpenAI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app import agent, delete, deploy, host, invoke
from app.config import Settings, get_settings

SETTINGS = Settings(
    "https://example.services.ai.azure.com/api/projects/demo", "workshop-model",
    workiq_connection_name="workshop-workiq-mcp",
    workiq_connection_id="/projects/demo/connections/workshop-workiq-mcp",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    for name in (
        "FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL_DEPLOYMENT", "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        "WORKIQ_CONNECTION_NAME", "WORKIQ_CONNECTION_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def clients(monkeypatch, module, argv):
    project = MagicMock()
    project.__enter__.return_value = project
    project.connections.get.return_value = SimpleNamespace(
        target=agent.WORKIQ_MCP_URL, id=SETTINGS.workiq_connection_id,
    )
    project.agents.create_version_from_code.return_value = SimpleNamespace(
        name=agent.AGENT_NAME, version="3",
    )
    project.agents.get_version.return_value = {"status": "active"}
    project.agents.get.return_value = SimpleNamespace(agent_endpoint=None)
    client = project.get_openai_client.return_value.__enter__.return_value
    client.responses.create.return_value = response()
    monkeypatch.setattr(module, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(module, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(module, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", argv)
    return project, client


def response(*output, status="completed"):
    return SimpleNamespace(
        id="resp_test", status=status, output=list(output), output_text="Architecture",
        error="Access denied" if status != "completed" else None, incomplete_details=None,
    )


def expected_tools():
    return [
        {"type": "mcp", "server_label": "MicrosoftLearn",
         "server_url": agent.MSLEARN_MCP_URL, "require_approval": "never"},
        {"type": "mcp", "server_label": "WorkIQ",
         "server_url": agent.WORKIQ_MCP_URL, "require_approval": "never",
         "project_connection_id": SETTINGS.workiq_connection_id},
    ]


def test_configuration_requires_endpoint_and_supports_hosted_model(monkeypatch):
    with pytest.raises(RuntimeError, match="FOUNDRY_PROJECT_ENDPOINT"):
        get_settings()
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", SETTINGS.project_endpoint)
    assert get_settings().model_deployment == "gpt-5.3-codex-2"
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "hosted-model")
    assert get_settings().model_deployment == "hosted-model"
    monkeypatch.setenv("FOUNDRY_MODEL_DEPLOYMENT", " local-model ")
    assert get_settings().model_deployment == "local-model"


def test_workiq_configuration_separates_deployment_name_and_runtime_id(monkeypatch):
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", SETTINGS.project_endpoint)
    assert get_settings().workiq_connection_name == ""
    assert get_settings().workiq_connection_id == ""
    monkeypatch.setenv("WORKIQ_CONNECTION_NAME", " workshop-workiq-mcp ")
    monkeypatch.setenv("WORKIQ_CONNECTION_ID", f" {SETTINGS.workiq_connection_id} ")
    assert get_settings().workiq_connection_name == SETTINGS.workiq_connection_name
    assert get_settings().workiq_connection_id == SETTINGS.workiq_connection_id


def test_agent_wires_full_workiq_and_learn_catalogs_without_toolbox(monkeypatch):
    factory = MagicMock()
    client_factory = MagicMock()
    monkeypatch.setattr(agent, "Agent", factory)
    monkeypatch.setattr(agent, "FoundryChatClient", client_factory)
    context = MagicMock()
    context.platform_headers.return_value = {"x-agent-foundry-call-id": "opaque-call"}
    monkeypatch.setattr(agent, "get_request_context", lambda: context)
    credential = MagicMock()

    agent.create_agent(SETTINGS, credential)

    client_factory.assert_called_once_with(
        project_endpoint=SETTINGS.project_endpoint, model="workshop-model", credential=credential,
        default_headers={"x-agent-foundry-call-id": "opaque-call"},
        middleware=[agent.trace_managed_mcp],
    )
    options = factory.call_args.kwargs
    assert options["name"] == "mea-hosted-agent"
    assert "Mermaid" in options["instructions"]
    assert "never overwrite existing files" in " ".join(options["instructions"].split())
    assert "without asking for tool-use\napproval or confirmation" in options["instructions"]
    assert options["default_options"] == {"store": False}
    assert [tool.as_dict() for tool in options["tools"]] == expected_tools()


@pytest.mark.parametrize("connection_id", ["", " "])
def test_runtime_requires_workiq_connection_id(connection_id):
    with pytest.raises(ValueError, match="WORKIQ_CONNECTION_ID"):
        agent.create_tools(replace(SETTINGS, workiq_connection_id=connection_id))


def test_deploy_requires_workiq_connection_name(monkeypatch, capsys):
    project, _ = clients(monkeypatch, deploy, ["deploy"])
    monkeypatch.setattr(deploy, "get_settings", lambda: replace(SETTINGS, workiq_connection_name=""))
    with pytest.raises(SystemExit) as exc:
        deploy.main()
    assert exc.value.code == 2
    assert "WORKIQ_CONNECTION_NAME" in capsys.readouterr().err
    project.agents.create_version_from_code.assert_not_called()


@pytest.mark.parametrize("target", [
    "https://example.com/mcp", "https://workiq.svc.cloud.microsoft/a2a",
])
def test_deploy_rejects_unrelated_or_preview_connection_before_upload(monkeypatch, target):
    project, _ = clients(monkeypatch, deploy, ["deploy"])
    project.connections.get.return_value.target = target
    with pytest.raises(RuntimeError, match="Work IQ connection must target"):
        deploy.main()
    project.agents.create_version_from_code.assert_not_called()
    project.agents.update_details.assert_not_called()


@pytest.mark.parametrize("connection_id", [None, "", " "])
def test_deploy_rejects_empty_connection_id(monkeypatch, connection_id):
    project, _ = clients(monkeypatch, deploy, ["deploy"])
    project.connections.get.return_value.id = connection_id
    with pytest.raises(ValueError, match="connection ID"):
        deploy.main()
    project.agents.create_version_from_code.assert_not_called()


def test_connection_lookup_failure_is_not_replaced_by_a_fallback(monkeypatch):
    project, _ = clients(monkeypatch, deploy, ["deploy"])
    project.connections.get.side_effect = RuntimeError("Connection access denied")
    with pytest.raises(RuntimeError, match="Connection access denied"):
        deploy.main()
    project.agents.create_version_from_code.assert_not_called()


@pytest.mark.parametrize("trailing_slash", [False, True])
def test_deploy_resolves_connection_packages_runtime_and_routes(monkeypatch, trailing_slash):
    project, _ = clients(monkeypatch, deploy, ["deploy", "--name", "custom-hosted"])
    if trailing_slash:
        project.connections.get.return_value.target += "/"
    project.connections.get.return_value.id = "/resolved/connection"
    deploy.main()

    project.connections.get.assert_called_once_with(SETTINGS.workiq_connection_name)
    upload = project.agents.create_version_from_code.call_args.kwargs
    assert upload["agent_name"] == "custom-hosted"
    assert upload["code"].closed
    assert not Path(upload["code"].name).parent.exists()
    definition = upload["definition"].as_dict()
    assert definition["kind"] == "hosted"
    assert definition["protocol_versions"] == [{"protocol": "responses", "version": "2.0.0"}]
    assert definition["code_configuration"] == {
        "runtime": "python_3_14", "entry_point": ["python", "main.py"],
        "dependency_resolution": "remote_build",
    }
    assert definition["environment_variables"] == {
        "FOUNDRY_PROJECT_ENDPOINT": SETTINGS.project_endpoint,
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "workshop-model",
        "WORKIQ_CONNECTION_ID": "/resolved/connection",
    }
    project.agents.get_version.assert_called_once_with(agent_name="custom-hosted", agent_version="3")
    route = project.agents.update_details.call_args.kwargs
    assert route["agent_name"] == "custom-hosted"
    assert route["agent_endpoint"].as_dict() == {
        "version_selector": {"version_selection_rules": [
            {"type": "FixedRatio", "agent_version": "3", "traffic_percentage": 100},
        ]},
        "protocol_configuration": {"responses": {}},
    }
    project.agents.create_version.assert_not_called()
    project.connections.get_credentials.assert_not_called()


def test_zip_allowlist_excludes_secrets_and_entry_point_runs_host(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parent.parent
    staged = tmp_path / "source"
    expected = {
        "main.py", "requirements.txt", "app/__init__.py",
        "app/agent.py", "app/config.py", "app/host.py",
    }
    for name in expected:
        target = staged / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / name).read_bytes())
    (staged / ".env").write_text("SECRET=must-not-upload")
    (staged / "app" / "token.json").write_text("must-not-upload")
    zip_path = tmp_path / "agent.zip"
    deploy.create_code_zip(staged, zip_path)
    with ZipFile(zip_path) as archive:
        assert set(archive.namelist()) == expected
    main = MagicMock()
    monkeypatch.setattr(host, "main", main)
    runpy.run_path(str(source / "main.py"), run_name="__main__")
    main.assert_called_once_with()


def test_failed_build_does_not_replace_routing_and_cleans_zip(monkeypatch):
    project, _ = clients(monkeypatch, deploy, ["deploy"])
    project.agents.get_version.return_value = {"status": "failed", "error": "Remote build failed"}
    with pytest.raises(RuntimeError, match="Remote build failed"):
        deploy.main()
    project.agents.update_details.assert_not_called()
    code = project.agents.create_version_from_code.call_args.kwargs["code"]
    assert code.closed
    assert not Path(code.name).parent.exists()


def test_redeploy_preserves_published_protocols_and_authorization(monkeypatch):
    project, _ = clients(monkeypatch, deploy, ["deploy"])
    existing = AgentEndpointConfig({
        "protocol_configuration": {"activity": {}, "responses": {}},
        "authorization_schemes": [{"type": "BotServiceTenant"}],
    })
    project.agents.get.return_value = SimpleNamespace(agent_endpoint=existing)
    deploy.main()
    endpoint = project.agents.update_details.call_args.kwargs["agent_endpoint"].as_dict()
    assert endpoint["protocol_configuration"] == {"activity": {}, "responses": {}}
    assert endpoint["authorization_schemes"] == [{"type": "BotServiceTenant"}]


def test_provisioning_wait_and_timeout(monkeypatch):
    project = MagicMock()
    project.agents.get_version.side_effect = [{"status": "creating"}, {"status": "active"}]
    monkeypatch.setattr(deploy.time, "sleep", MagicMock())
    deploy.wait_until_active(project, agent.AGENT_NAME, "3")
    deploy.time.sleep.assert_called_once_with(5)
    monkeypatch.setattr(deploy.time, "monotonic", MagicMock(side_effect=[0, 0, 601]))
    project.agents.get_version.side_effect = None
    project.agents.get_version.return_value = {"status": "creating"}
    with pytest.raises(TimeoutError):
        deploy.wait_until_active(project, agent.AGENT_NAME, "3")


def test_host_factory_is_request_scoped_and_closes_credential(monkeypatch):
    credential = MagicMock()
    credential.__enter__.return_value = credential
    factory = MagicMock(side_effect=[MagicMock(), MagicMock()])
    server = MagicMock()
    monkeypatch.setattr(host, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(host, "DefaultAzureCredential", lambda: credential)
    monkeypatch.setattr(host, "create_agent", factory)
    monkeypatch.setattr(host, "ResponsesHostServer", server)
    host.main()
    request_factory = server.call_args.kwargs["agent"]
    assert request_factory() is not request_factory()
    factory.assert_called_with(SETTINGS, credential)
    server.return_value.run.assert_called_once_with()
    credential.__exit__.assert_called_once()


def test_invoke_uses_hosted_endpoint_without_interactive_approval(monkeypatch, capsys):
    project, client = clients(monkeypatch, invoke, ["invoke", "--name", "custom-hosted", "Create a new file"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    read_input = MagicMock()
    monkeypatch.setattr("builtins.input", read_input)
    invoke.main()
    project.get_openai_client.assert_called_once_with(agent_name="custom-hosted")
    client.responses.create.assert_called_once_with(input="Create a new file", store=True)
    read_input.assert_not_called()
    assert "Architecture" in capsys.readouterr().out


@pytest.mark.parametrize("status", ["failed", "incomplete"])
def test_invoke_reports_incomplete_and_failed_responses(monkeypatch, status):
    _, client = clients(monkeypatch, invoke, ["invoke"])
    client.responses.create.return_value = response(status=status)
    with pytest.raises(RuntimeError, match="Access denied"):
        invoke.main()


@pytest.mark.parametrize(("item", "message"), [
    (SimpleNamespace(type="mcp_call", error="Tenant denial"), "Tenant denial"),
    (SimpleNamespace(type="oauth_consent_request"), "OAuth consent is required"),
    (SimpleNamespace(type="mcp_approval_request"), "Unexpected MCP approval"),
])
def test_invoke_surfaces_mcp_failures_without_claiming_success(monkeypatch, capsys, item, message):
    _, client = clients(monkeypatch, invoke, ["invoke"])
    client.responses.create.return_value = response(item)
    with pytest.raises(RuntimeError, match=message):
        invoke.main()
    assert "Architecture" not in capsys.readouterr().out
    assert client.responses.create.call_count == 1


def test_delete_removes_only_selected_agent(monkeypatch):
    project, _ = clients(monkeypatch, delete, ["delete", "--name", "custom-hosted"])
    delete.main()
    project.agents.delete.assert_called_once_with(agent_name="custom-hosted")
    project.connections.delete.assert_not_called()


def test_real_responses_host_forwards_direct_mcp_payload_and_caller_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    requests = []
    model_clients = []
    real_client = agent.FoundryChatClient
    exporter = InMemorySpanExporter()
    with monkeypatch.context() as telemetry_env:
        telemetry_env.delenv("OTEL_SDK_DISABLED")
        provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)
    monkeypatch.setattr(agent, "_TRACER", tracer)

    def transport(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == SETTINGS.model_deployment
        assert payload["tools"] == expected_tools()
        assert payload["store"] is False
        assert request.headers["x-agent-foundry-call-id"] == f"call-{len(requests)}"
        message = {
            "id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "Architecture", "annotations": []}],
        }
        tool_call = {
            "type": "mcp_call", "id": "mcp_test", "name": "learn_search", "server_label": "MicrosoftLearn",
            "arguments": '{"query":"private-argument"}', "output": "private-result", "status": "completed",
        }
        failed_call = {
            "type": "mcp_call", "id": "mcp_failed", "name": "workiq_search", "server_label": "WorkIQ",
            "arguments": "{}", "status": "failed",
        }
        events = [
            {"type": "response.output_text.delta", "sequence_number": 0,
             "item_id": "msg_test", "output_index": 2, "content_index": 0, "delta": "Architecture"},
            {"type": "response.output_item.done", "sequence_number": 1, "output_index": 0, "item": tool_call},
            {"type": "response.output_item.done", "sequence_number": 2, "output_index": 0, "item": tool_call},
            {"type": "response.output_item.done", "sequence_number": 3, "output_index": 1, "item": failed_call},
            {"type": "response.output_item.done", "sequence_number": 4, "output_index": 2, "item": message},
            {"type": "response.completed", "sequence_number": 5, "response": {
                "id": "resp_model", "object": "response", "created_at": 0, "status": "completed",
                "model": SETTINGS.model_deployment, "output": [tool_call, failed_call, message],
            }},
        ]
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"},
            text="".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n",
        )

    def create_client(**kwargs):
        http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(transport))
        project = MagicMock()
        project.get_openai_client.side_effect = lambda **options: AsyncOpenAI(
            api_key="offline-model-token", base_url=f"{SETTINGS.project_endpoint}/openai/v1",
            http_client=http_client, **options,
        )
        client = real_client(project_client=project, **kwargs)
        model_clients.append(http_client)
        return client

    monkeypatch.setattr(agent, "FoundryChatClient", create_client)
    credential = MagicMock()

    async def scenario():
        server = ResponsesHostServer(agent=lambda: agent.create_agent(SETTINGS, credential))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://test") as client:
            ready = await client.get("/readiness")
            assert ready.status_code == 200
            for number in (1, 2):
                with tracer.start_as_current_span("request"):
                    headers = {"x-agent-foundry-call-id": f"call-{number}"}
                    TraceContextTextMapPropagator().inject(headers)
                    result = await client.post(
                        "/responses", json={"input": "Design an architecture", "stream": False, "store": False},
                        headers=headers,
                    )
                assert result.status_code == 200, result.text
                assert result.json()["status"] == "completed", result.text
                assert "Architecture" in result.text
                assert not any(item["type"] == "mcp_approval_request" for item in result.json()["output"])

    try:
        asyncio.run(scenario())
        spans = exporter.get_finished_spans()
    finally:
        provider.shutdown()
    assert len(requests) == 2
    assert all(client.is_closed for client in model_clients)
    credential.get_token.assert_not_called()
    request_spans = [span for span in spans if span.name == "request"]
    assert len(request_spans) == 2
    for request_span in request_spans:
        tools = [
            span for span in spans
            if span.name.startswith("execute_tool ") and span.context.trace_id == request_span.context.trace_id
        ]
        assert [span.name for span in tools] == ["execute_tool learn_search", "execute_tool workiq_search"], [
            span.name for span in spans
        ]
        assert dict(tools[0].attributes) == {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "learn_search",
            "gen_ai.tool.type": "mcp",
            "gen_ai.tool.call.id": "mcp_test",
        }
        assert tools[1].status.status_code == StatusCode.ERROR
        assert tools[1].attributes["error.type"] == "mcp_call_failed"


def test_non_streaming_telemetry_only_reports_terminal_mcp_calls(monkeypatch):
    tracer = MagicMock()
    monkeypatch.setattr(agent, "_TRACER", tracer)
    items = [
        SimpleNamespace(type="mcp_list_tools", id="list", status="completed"),
        SimpleNamespace(type="mcp_approval_request", id="approval", name="search", status="completed"),
        SimpleNamespace(type="mcp_call", id="running", name="search", status="in_progress"),
        SimpleNamespace(type="mcp_call", id="done", name="search", status=None, output=""),
        SimpleNamespace(type="mcp_call", id="denied", name="search", error="private-error-text"),
    ]
    response = ChatResponse(messages=[], raw_representation=SimpleNamespace(output=items))
    context = ChatContext(client=MagicMock(), messages=[], options=None)

    async def call_next():
        context.result = response

    asyncio.run(agent.trace_managed_mcp(context, call_next))

    assert context.result is response
    assert [
        call.kwargs["attributes"]["gen_ai.tool.call.id"]
        for call in tracer.start_as_current_span.call_args_list
    ] == ["done", "denied"]
    assert "private-error-text" not in repr(tracer.mock_calls)
