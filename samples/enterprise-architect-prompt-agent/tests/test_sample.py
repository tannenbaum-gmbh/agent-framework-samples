from unittest.mock import MagicMock

import pytest

from app import create, delete, invoke
from app.agent import AGENT_NAME, INSTRUCTIONS, VECTOR_STORE_NAME, create_definition
from app.config import Settings, get_settings

SETTINGS = Settings(
    project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
    model_deployment="gpt-5.3-codex-2",
    a2a_connection_name="mslearn-a2a",
)


def clients() -> tuple[MagicMock, MagicMock]:
    project = MagicMock()
    project.__enter__.return_value = project
    openai = MagicMock()
    openai.__enter__.return_value = openai
    project.get_openai_client.return_value = openai
    return project, openai


def test_definition_combines_a2a_and_file_search() -> None:
    definition = create_definition(
        SETTINGS,
        a2a_connection_id="/connections/mslearn-a2a",
        vector_store_id="vs_guidelines",
    ).as_dict()

    assert definition["kind"] == "prompt"
    assert definition["model"] == "gpt-5.3-codex-2"
    assert definition["instructions"] == INSTRUCTIONS
    assert definition["tools"] == [
        {
            "type": "a2a",
            "a2a_version": "1.0",
            "project_connection_id": "/connections/mslearn-a2a",
        },
        {
            "type": "file_search",
            "vector_store_ids": ["vs_guidelines"],
        },
    ]


def test_create_uploads_guidelines_and_registers_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, openai = clients()
    project.connections.get.return_value.id = "/connections/mslearn-a2a"
    openai.vector_stores.create.return_value.id = "vs_guidelines"
    project.agents.create_version.return_value.name = AGENT_NAME
    project.agents.create_version.return_value.version = "1"
    paths = create.guideline_paths()
    monkeypatch.setattr(create, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(create, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(create, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["create"])

    create.main()

    project.connections.get.assert_called_once_with("mslearn-a2a")
    openai.vector_stores.create.assert_called_once_with(name=VECTOR_STORE_NAME)
    assert openai.vector_stores.files.upload_and_poll.call_count == len(paths)
    call = project.agents.create_version.call_args.kwargs
    assert call["agent_name"] == AGENT_NAME
    assert call["definition"].as_dict()["tools"][1]["vector_store_ids"] == [
        "vs_guidelines"
    ]


def test_create_deletes_vector_store_when_agent_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, openai = clients()
    project.connections.get.return_value.id = "/connections/mslearn-a2a"
    openai.vector_stores.create.return_value.id = "vs_guidelines"
    project.agents.create_version.side_effect = RuntimeError("create failed")
    monkeypatch.setattr(create, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(create, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(create, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["create"])

    with pytest.raises(RuntimeError, match="create failed"):
        create.main()

    openai.vector_stores.delete.assert_called_once_with("vs_guidelines")


def test_invoke_references_registered_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    project, openai = clients()
    response = openai.responses.create.return_value
    response.status = "completed"
    response.output_text = "Architecture recommendation"
    monkeypatch.setattr(invoke, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(invoke, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(invoke, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["invoke", "Design a tier-1 platform"])

    invoke.main()

    openai.responses.create.assert_called_once_with(
        input="Design a tier-1 platform",
        tool_choice="required",
        extra_body={
            "agent_reference": {
                "name": AGENT_NAME,
                "type": "agent_reference",
            }
        },
    )


def test_invoke_debug_prints_complete_response(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project, openai = clients()
    response = openai.responses.create.return_value
    response.status = "completed"
    response.output_text = "Authorization required"
    response.model_dump_json.return_value = '{"output":[{"type":"a2a_call"}]}'
    monkeypatch.setattr(invoke, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(invoke, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(invoke, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr(
        "sys.argv",
        ["invoke", "--debug", "Ask the documentation agent"],
    )

    invoke.main()

    captured = capsys.readouterr()
    assert captured.out == "Authorization required\n"
    assert captured.err == '{"output":[{"type":"a2a_call"}]}\n'


def test_delete_removes_agent_and_vector_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, openai = clients()
    monkeypatch.setattr(delete, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(delete, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(delete, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr(
        "sys.argv",
        ["delete", "--version", "2", "--vector-store", "vs_guidelines"],
    )

    delete.main()

    project.agents.delete_version.assert_called_once_with(
        agent_name=AGENT_NAME,
        agent_version="2",
    )
    openai.vector_stores.delete.assert_called_once_with("vs_guidelines")


def test_settings_require_a2a_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", SETTINGS.project_endpoint)
    monkeypatch.delenv("MSLEARN_A2A_CONNECTION_NAME", raising=False)

    with pytest.raises(RuntimeError, match="MSLEARN_A2A_CONNECTION_NAME"):
        get_settings()


def test_settings_default_model_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", SETTINGS.project_endpoint)
    monkeypatch.setenv("MSLEARN_A2A_CONNECTION_NAME", "mslearn-a2a")
    monkeypatch.delenv("FOUNDRY_MODEL_DEPLOYMENT", raising=False)

    assert get_settings().model_deployment == "gpt-5.3-codex-2"
