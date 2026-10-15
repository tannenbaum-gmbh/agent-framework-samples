from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from agent_framework import Agent, BaseChatClient, ChatResponse, ChatResponseUpdate, Content, Message, ResponseStream
from agent_framework_foundry_hosting import ResponsesHostServer

from app import agent, deploy, host, invoke, run_agent
from app.config import Settings, get_settings

SETTINGS = Settings("https://example.services.ai.azure.com/api/projects/demo", "gpt-5")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "FOUNDRY_PROJECT_ENDPOINT",
        "FOUNDRY_MODEL_DEPLOYMENT",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


def test_endpoint_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", " ")
    with pytest.raises(RuntimeError, match="FOUNDRY_PROJECT_ENDPOINT"):
        get_settings()


@pytest.mark.parametrize(
    ("local_model", "hosted_model", "expected"),
    [(None, None, "gpt-5"), (None, "hosted-model", "hosted-model"), ("local-model", "hosted-model", "local-model")],
)
def test_model_configuration(
    monkeypatch: pytest.MonkeyPatch, local_model: str | None, hosted_model: str | None, expected: str
) -> None:
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", SETTINGS.project_endpoint)
    if local_model is not None:
        monkeypatch.setenv("FOUNDRY_MODEL_DEPLOYMENT", local_model)
    if hosted_model is not None:
        monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", hosted_model)
    assert get_settings() == Settings(SETTINGS.project_endpoint, expected)


def test_agent_uses_foundry_and_public_mslearn_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    client_factory = MagicMock()
    tool_factory = MagicMock()
    agent_factory = MagicMock()
    monkeypatch.setattr(agent, "FoundryChatClient", client_factory)
    monkeypatch.setattr(agent, "MCPStreamableHTTPTool", tool_factory)
    monkeypatch.setattr(agent, "Agent", agent_factory)
    credential = MagicMock()

    agent.create_agent(SETTINGS, credential)

    client_factory.assert_called_once_with(
        project_endpoint=SETTINGS.project_endpoint, model="gpt-5", credential=credential
    )
    tool_factory.assert_called_once_with(
        name="MicrosoftLearn", url="https://learn.microsoft.com/api/mcp", load_prompts=False
    )
    assert agent_factory.call_args.kwargs["tools"] == [tool_factory.return_value]
    assert "Include links" in agent_factory.call_args.kwargs["instructions"]


@pytest.mark.parametrize("fail", [False, True])
def test_cli_enters_and_closes_agent_and_credential(monkeypatch: pytest.MonkeyPatch, fail: bool) -> None:
    credential = MagicMock()
    credential.__enter__.return_value = credential
    local_agent = MagicMock()
    local_agent.__aenter__ = AsyncMock(return_value=local_agent)
    local_agent.__aexit__ = AsyncMock(return_value=False)
    local_agent.run = AsyncMock(return_value="Documented answer")
    if fail:
        local_agent.run.side_effect = RuntimeError("MCP unavailable")
    factory = MagicMock(return_value=local_agent)
    monkeypatch.setattr(run_agent, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(run_agent, "DefaultAzureCredential", lambda: credential)
    monkeypatch.setattr(run_agent, "create_agent", factory)

    if fail:
        with pytest.raises(RuntimeError, match="MCP unavailable"):
            asyncio.run(run_agent.run("Explain hosting"))
    else:
        assert asyncio.run(run_agent.run("Explain hosting")) == "Documented answer"
    factory.assert_called_once_with(SETTINGS, credential)
    local_agent.run.assert_awaited_once_with("Explain hosting")
    local_agent.__aenter__.assert_awaited_once()
    local_agent.__aexit__.assert_awaited_once()
    credential.__exit__.assert_called_once()


def test_host_uses_request_scoped_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = MagicMock()
    credential.__enter__.return_value = credential
    factory = MagicMock(side_effect=[MagicMock(), MagicMock()])
    server_factory = MagicMock()
    monkeypatch.setattr(host, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(host, "DefaultAzureCredential", lambda: credential)
    monkeypatch.setattr(host, "create_agent", factory)
    monkeypatch.setattr(host, "ResponsesHostServer", server_factory)

    host.main()

    request_factory = server_factory.call_args.kwargs["agent"]
    assert request_factory() is not request_factory()
    assert factory.call_count == 2
    server_factory.return_value.run.assert_called_once_with()
    credential.__exit__.assert_called_once()


class FakeChatClient(BaseChatClient):
    STORES_BY_DEFAULT = False

    def _inner_get_response(
        self, *, messages: Sequence[Message], stream: bool, options: Mapping[str, object], **kwargs: object
    ) -> ResponseStream[ChatResponseUpdate, ChatResponse]:
        assert stream

        async def updates() -> AsyncIterator[ChatResponseUpdate]:
            yield ChatResponseUpdate(
                role="assistant",
                contents=[Content.from_text("An answer with https://learn.microsoft.com/")],
            )

        return ResponseStream(updates(), finalizer=ChatResponse.from_updates)


def test_real_responses_host_serves_offline_agent() -> None:
    async def scenario() -> None:
        created: list[Agent] = []

        def factory() -> Agent:
            fresh_agent = Agent(client=FakeChatClient(), name="mslearn-agent")
            created.append(fresh_agent)
            return fresh_agent

        server = ResponsesHostServer(agent=factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://test") as client:
            readiness = await client.get("/readiness")
            assert readiness.status_code == 200
            for _ in range(2):
                response = await client.post(
                    "/responses", json={"input": "Explain hosting", "stream": False, "store": False}
                )
                assert response.status_code == 200, response.text
                assert response.json()["status"] == "completed", response.text
                assert "https://learn.microsoft.com/" in str(response.json()["output"])
        assert len(created) == 2
        assert created[0] is not created[1]

    asyncio.run(scenario())


def test_deploy_registers_responses_container(monkeypatch: pytest.MonkeyPatch) -> None:
    project = MagicMock()
    project.__enter__.return_value = project
    project.agents.create_version.return_value.name = "mslearn-agent"
    project.agents.create_version.return_value.version = "3"
    project.agents.get_version.return_value = {"status": "active"}
    monkeypatch.setattr(deploy, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(deploy, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(deploy, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["deploy", "--image", "example.azurecr.io/mslearn-agent:v1"])

    deploy.main()

    definition = project.agents.create_version.call_args.kwargs["definition"].as_dict()
    assert definition["kind"] == "hosted"
    assert definition["protocol_versions"] == [{"protocol": "responses", "version": "2.0.0"}]
    assert definition["container_configuration"]["image"] == "example.azurecr.io/mslearn-agent:v1"
    assert definition["environment_variables"] == {
        "FOUNDRY_PROJECT_ENDPOINT": SETTINGS.project_endpoint,
        "FOUNDRY_MODEL_DEPLOYMENT": "gpt-5",
    }
    project.agents.get_version.assert_called_once_with(agent_name="mslearn-agent", agent_version="3")
    project.__exit__.assert_called_once()


@pytest.mark.parametrize("status", ["failed", "deleted", "deleting"])
def test_provisioning_failure_is_explicit(status: str) -> None:
    project = MagicMock()
    project.agents.get_version.return_value = {"status": status, "error": "Image pull failed"}
    with pytest.raises(RuntimeError, match="Image pull failed"):
        deploy.wait_until_active(project, "mslearn-agent", "1")


def test_provisioning_waits_for_active(monkeypatch: pytest.MonkeyPatch) -> None:
    project = MagicMock()
    project.agents.get_version.side_effect = [{"status": "creating"}, {"status": "active"}]
    sleep = MagicMock()
    monkeypatch.setattr(deploy.time, "sleep", sleep)
    deploy.wait_until_active(project, "mslearn-agent", "1")
    assert project.agents.get_version.call_count == 2
    sleep.assert_called_once_with(5)


def test_provisioning_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    project = MagicMock()
    project.agents.get_version.return_value = {"status": "creating"}
    monkeypatch.setattr(deploy.time, "monotonic", MagicMock(side_effect=[0, 0, 601]))
    monkeypatch.setattr(deploy.time, "sleep", MagicMock())
    with pytest.raises(TimeoutError, match="did not become active"):
        deploy.wait_until_active(project, "mslearn-agent", "1")


def test_invoke_uses_hosted_agent_endpoint(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    project = MagicMock()
    project.__enter__.return_value = project
    client = project.get_openai_client.return_value.__enter__.return_value
    client.responses.create.return_value.status = "completed"
    client.responses.create.return_value.output_text = "Hosted answer"
    monkeypatch.setattr(invoke, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(invoke, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(invoke, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["invoke", "Explain hosting"])

    invoke.main()

    project.get_openai_client.assert_called_once_with(agent_name="mslearn-agent")
    client.responses.create.assert_called_once_with(input="Explain hosting")
    assert capsys.readouterr().out.strip() == "Hosted answer"


@pytest.mark.parametrize("status", ["failed", "incomplete"])
def test_invoke_surfaces_response_failures(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    project = MagicMock()
    project.__enter__.return_value = project
    client = project.get_openai_client.return_value.__enter__.return_value
    client.responses.create.return_value.status = status
    client.responses.create.return_value.error = "Model access denied"
    monkeypatch.setattr(invoke, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(invoke, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(invoke, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["invoke"])

    with pytest.raises(RuntimeError, match="Model access denied"):
        invoke.main()
