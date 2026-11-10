from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import agent, create, delete
from app.config import IdentitySettings, Settings

SETTINGS = Settings(
    foundry_project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
    foundry_model_deployment="workshop-model",
    subscription_id="sub",
    resource_group="rg",
    sandbox_group="sandbox",
    sandbox_region="westus2",
    sandbox_disk_image="python-3.14",
    identity=IdentitySettings(tenant_id="tenant", blueprint_app_id="blueprint", agent_identity_client_id="agent"),
    workiq_connection_name="workshop-workiq-mcp",
)


def test_definition_uses_full_mcp_catalogs_without_approval() -> None:
    definition = agent.create_definition(SETTINGS, workiq_connection_id="/connections/workiq").as_dict()
    assert definition["kind"] == "prompt"
    assert definition["model"] == "workshop-model"
    assert definition["instructions"] == agent.INSTRUCTIONS
    assert definition["tools"] == [
        {
            "type": "mcp",
            "server_label": "MicrosoftLearn",
            "server_url": agent.MSLEARN_MCP_URL,
            "require_approval": "never",
        },
        {
            "type": "mcp",
            "server_label": "WorkIQ",
            "server_url": agent.WORKIQ_MCP_URL,
            "project_connection_id": "/connections/workiq",
            "require_approval": "never",
        },
    ]
    assert "including SharePoint" in agent.INSTRUCTIONS
    assert "SEC-*, REL-*, GOV-*, and COST-*" in agent.INSTRUCTIONS
    assert "without asking for tool-use\napproval or confirmation" in agent.INSTRUCTIONS


@pytest.mark.parametrize("connection_id", ["", " ", "\t"])
def test_definition_requires_workiq_connection(connection_id: str) -> None:
    with pytest.raises(ValueError, match="Work IQ requires a connection ID"):
        agent.create_definition(SETTINGS, workiq_connection_id=connection_id)


def test_create_checks_workiq_target_and_creates_version(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    project = MagicMock()
    project.__enter__.return_value = project
    project.connections.get.return_value = SimpleNamespace(
        target=agent.WORKIQ_MCP_URL + "/", id="/connections/workiq"
    )
    project.agents.create_version.return_value = SimpleNamespace(name=agent.AGENT_NAME, version="7")
    monkeypatch.setattr(create, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(create, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(create, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["create"])

    create.main()

    kwargs = project.agents.create_version.call_args.kwargs
    assert kwargs["agent_name"] == agent.AGENT_NAME
    assert kwargs["definition"].as_dict()["tools"][1]["project_connection_id"] == "/connections/workiq"
    assert "version 7" in capsys.readouterr().out


def test_create_rejects_wrong_workiq_target(monkeypatch: pytest.MonkeyPatch) -> None:
    project = MagicMock()
    project.__enter__.return_value = project
    project.connections.get.return_value = SimpleNamespace(
        target="https://example.invalid/mcp", id="/connections/wrong"
    )
    monkeypatch.setattr(create, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(create, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(create, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["create"])

    with pytest.raises(RuntimeError, match="must target"):
        create.main()
    project.agents.create_version.assert_not_called()


def test_delete_removes_only_the_requested_version(monkeypatch: pytest.MonkeyPatch) -> None:
    project = MagicMock()
    project.__enter__.return_value = project
    monkeypatch.setattr(delete, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(delete, "DefaultAzureCredential", MagicMock())
    monkeypatch.setattr(delete, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr("sys.argv", ["delete", "--name", "custom-agent", "--version", "12"])

    delete.main()

    project.agents.delete_version.assert_called_once_with(agent_name="custom-agent", agent_version="12")
