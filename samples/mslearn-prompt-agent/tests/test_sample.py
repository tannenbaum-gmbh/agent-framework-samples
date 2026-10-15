from unittest.mock import MagicMock

import pytest

from app import create, delete, invoke
from app.agent import INSTRUCTIONS, MSLEARN_MCP_URL, create_definition
from app.config import Settings, get_settings

SETTINGS = Settings(
    project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
    model_deployment="gpt-5",
)


def project_client_mock() -> MagicMock:
    project = MagicMock()
    project.__enter__.return_value = project
    return project


def test_definition_uses_same_mslearn_behavior() -> None:
    definition = create_definition(SETTINGS).as_dict()

    assert definition["kind"] == "prompt"
    assert definition["model"] == "gpt-5"
    assert definition["instructions"] == INSTRUCTIONS
    assert definition["tools"] == [
        {
            "type": "mcp",
            "server_label": "MicrosoftLearn",
            "server_url": MSLEARN_MCP_URL,
            "require_approval": "never",
        }
    ]


def test_create_registers_persistent_version(monkeypatch: pytest.MonkeyPatch) -> None:
    project = project_client_mock()
    project.agents.create_version.return_value.name = "mslearn-prompt-agent"
    project.agents.create_version.return_value.version = "1"
    monkeypatch.setattr(create, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(create, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(create, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["create"])

    create.main()

    call = project.agents.create_version.call_args
    assert call.kwargs["agent_name"] == "mslearn-prompt-agent"
    assert call.kwargs["definition"].as_dict()["kind"] == "prompt"


def test_invoke_references_registered_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    project = project_client_mock()
    client = MagicMock()
    client.__enter__.return_value = client
    project.get_openai_client.return_value = client
    client.responses.create.return_value.status = "completed"
    client.responses.create.return_value.output_text = "Grounded answer"
    monkeypatch.setattr(invoke, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(invoke, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(invoke, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["invoke", "What is Agent Framework?"])

    invoke.main()

    client.responses.create.assert_called_once_with(
        input="What is Agent Framework?",
        extra_body={
            "agent_reference": {
                "name": "mslearn-prompt-agent",
                "type": "agent_reference",
            }
        },
    )


def test_invoke_rejects_incomplete_response(monkeypatch: pytest.MonkeyPatch) -> None:
    project = project_client_mock()
    client = MagicMock()
    client.__enter__.return_value = client
    project.get_openai_client.return_value = client
    response = client.responses.create.return_value
    response.status = "incomplete"
    response.error = None
    response.incomplete_details = "max_output_tokens"
    monkeypatch.setattr(invoke, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(invoke, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(invoke, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["invoke"])

    with pytest.raises(RuntimeError, match="max_output_tokens"):
        invoke.main()


def test_delete_removes_requested_version(monkeypatch: pytest.MonkeyPatch) -> None:
    project = project_client_mock()
    monkeypatch.setattr(delete, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(delete, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(delete, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["delete", "--version", "2"])

    delete.main()

    project.agents.delete_version.assert_called_once_with(
        agent_name="mslearn-prompt-agent",
        agent_version="2",
    )


def test_settings_require_project_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)

    with pytest.raises(RuntimeError, match="FOUNDRY_PROJECT_ENDPOINT"):
        get_settings()
