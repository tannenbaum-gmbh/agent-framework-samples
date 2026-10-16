import pytest

from app.config import get_settings


def test_endpoint_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    with pytest.raises(RuntimeError, match="FOUNDRY_PROJECT_ENDPOINT"):
        get_settings()


def test_defaults_and_endpoint_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", " https://example.com/project/ ")
    monkeypatch.delenv("ARCHITECT_AGENT_NAME", raising=False)
    monkeypatch.delenv("MSLEARN_AGENT_NAME", raising=False)
    settings = get_settings()
    assert settings.architect_agent_name == "enterprise-architect-prompt-agent"
    assert settings.mslearn_a2a_endpoint == (
        "https://example.com/project/agents/mslearn-prompt-agent/endpoint/protocols/a2a"
    )


def test_custom_agent_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.com/project")
    monkeypatch.setenv("ARCHITECT_AGENT_NAME", " custom-architect ")
    monkeypatch.setenv("MSLEARN_AGENT_NAME", " custom-docs ")
    settings = get_settings()
    assert settings.architect_agent_name == "custom-architect"
    assert "/agents/custom-docs/" in settings.mslearn_a2a_endpoint
