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
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.projects.models import AgentEndpointConfig
from openai import AsyncOpenAI

from app import agent, delete, deploy, host, invoke
from app.config import Settings, get_settings
from app.purview import WorkshopUserMiddleware
from agent_framework.microsoft import PurviewChatPolicyMiddleware

SETTINGS = Settings(
    "https://example.services.ai.azure.com/api/projects/demo", "workshop-model",
    workiq_connection_name="workshop-workiq-mcp",
    workiq_connection_id="/projects/demo/connections/workshop-workiq-mcp",
    purview_tenant_id="11111111-1111-1111-1111-111111111111",
    purview_app_id="22222222-2222-2222-2222-222222222222",
    purview_user_id="33333333-3333-3333-3333-333333333333",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    for name in (
        "FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL_DEPLOYMENT", "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        "WORKIQ_CONNECTION_NAME", "WORKIQ_CONNECTION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PURVIEW_TENANT_ID", SETTINGS.purview_tenant_id)
    monkeypatch.setenv("PURVIEW_APP_ID", SETTINGS.purview_app_id)
    monkeypatch.setenv("PURVIEW_USER_ID", SETTINGS.purview_user_id)


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

    middleware = client_factory.call_args.kwargs["middleware"]
    assert isinstance(middleware[0], WorkshopUserMiddleware)
    assert isinstance(middleware[-1], PurviewChatPolicyMiddleware)
    client_factory.assert_called_once_with(
        project_endpoint=SETTINGS.project_endpoint, model="workshop-model", credential=credential,
        default_headers={"x-agent-foundry-call-id": "opaque-call"},
        middleware=middleware,
    )
    options = factory.call_args.kwargs
    assert options["name"] == "mea-purview-hosted-agent"
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
        "PURVIEW_TENANT_ID": SETTINGS.purview_tenant_id,
        "PURVIEW_APP_ID": SETTINGS.purview_app_id,
        "PURVIEW_USER_ID": SETTINGS.purview_user_id,
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
        "app/agent.py", "app/config.py", "app/host.py", "app/purview.py",
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


@pytest.mark.parametrize("block_activity", [None, "uploadText", "downloadText"])
@pytest.mark.parametrize("request_stream", [False, True])
def test_real_responses_host_forwards_direct_mcp_payload_and_caller_context(
    tmp_path, monkeypatch, purview_graph, purview_credential, block_activity, request_stream,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    purview_graph["block_activity"] = block_activity
    requests = []
    model_clients = []
    real_client = agent.FoundryChatClient

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
        events = [
            {"type": "response.output_text.delta", "sequence_number": 0,
             "item_id": "msg_test", "output_index": 0, "content_index": 0, "delta": "Architecture"},
            {"type": "response.output_item.done", "sequence_number": 1, "output_index": 0, "item": message},
            {"type": "response.completed", "sequence_number": 2, "response": {
                "id": "resp_model", "object": "response", "created_at": 0, "status": "completed",
                "model": SETTINGS.model_deployment, "output": [message],
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
    credential = purview_credential

    async def scenario():
        server = ResponsesHostServer(agent=lambda: agent.create_agent(SETTINGS, credential))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://test") as client:
            ready = await client.get("/readiness")
            assert ready.status_code == 200
            for number in (1, 2):
                result = await client.post(
                    "/responses", json={"input": "Design an architecture", "stream": request_stream, "store": False},
                    headers={"x-agent-foundry-call-id": f"call-{number}"},
                )
                assert result.status_code == 200, result.text
                if request_stream:
                    events = [
                        json.loads(line.removeprefix("data: "))
                        for line in result.text.splitlines()
                        if line.startswith("data: ") and line != "data: [DONE]"
                    ]
                    completed = [event["response"] for event in events if event["type"] == "response.completed"]
                    assert len(completed) == 1, result.text
                    body = completed[0]
                else:
                    body = result.json()
                assert body["status"] == "completed", result.text
                if block_activity:
                    expected = (
                        "Prompt blocked by policy" if block_activity == "uploadText"
                        else "Response blocked by policy"
                    )
                    assert expected in result.text
                    assert "Architecture" not in result.text
                else:
                    assert "Architecture" in result.text
                assert not any(item["type"] == "mcp_approval_request" for item in body["output"])

    asyncio.run(scenario())
    assert len(requests) == (0 if block_activity == "uploadText" else 2)
    assert all(client.is_closed for client in model_clients)
    credential.get_token.assert_called()
    assert any("/processContent" in str(request.url) for request in purview_graph["requests"])
