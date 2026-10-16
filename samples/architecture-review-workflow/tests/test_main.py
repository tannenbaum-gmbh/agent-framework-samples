from unittest.mock import AsyncMock, MagicMock

import pytest

from app import main
from app.config import Settings


@pytest.mark.parametrize("show_steps", [True, False])
@pytest.mark.parametrize("fail_review", [True, False])
async def test_cli_wires_agents_and_closes_clients(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    show_steps: bool,
    fail_review: bool,
) -> None:
    settings = Settings(project_endpoint="https://example.com/project")
    credential, project, responses, http, remote = (MagicMock() for _ in range(5))
    for resource in (credential, project, responses, http, remote):
        resource.__aenter__ = AsyncMock(return_value=resource)
        resource.__aexit__ = AsyncMock(return_value=False)
    project.get_openai_client.return_value = responses
    architect = MagicMock()
    architect.respond = AsyncMock(side_effect=["Draft", "Final design"])
    documentation = MagicMock()
    documentation.respond = AsyncMock(return_value="Review")
    if fail_review:
        documentation.respond.side_effect = RuntimeError("review unavailable")
    architect_factory = MagicMock(return_value=architect)
    docs_factory = MagicMock(return_value=documentation)
    remote_factory = MagicMock(return_value=remote)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "DefaultAzureCredential", lambda: credential)
    monkeypatch.setattr(main, "AIProjectClient", MagicMock(return_value=project))
    monkeypatch.setattr(main.httpx, "AsyncClient", MagicMock(return_value=http))
    monkeypatch.setattr(main, "ArchitectAgent", architect_factory)
    monkeypatch.setattr(main, "DocumentationAgent", docs_factory)
    monkeypatch.setattr(main, "A2AAgent", remote_factory)

    if fail_review:
        with pytest.raises(Exception, match="review unavailable"):
            await main._run("Platform request", show_steps)
    else:
        await main._run("Platform request", show_steps)

    architect_factory.assert_called_once_with(responses, settings.architect_agent_name)
    docs_factory.assert_called_once_with(remote)
    remote_factory.assert_called_once_with(
        name=settings.mslearn_agent_name,
        url=settings.mslearn_a2a_endpoint,
        http_client=http,
    )
    auth = main.httpx.AsyncClient.call_args.kwargs["auth"]
    assert isinstance(auth, main.AzureA2AAuth)
    output = capsys.readouterr().out
    if fail_review:
        assert output == ""
        assert architect.respond.await_count == 1
    else:
        assert "## Final architecture recommendation\n\nFinal design" in output
        assert ("## Architect draft\n\nDraft" in output) is show_steps
        assert ("## Microsoft Learn A2A review\n\nReview" in output) is show_steps
    for resource in (credential, project, responses, http, remote):
        resource.__aexit__.assert_awaited_once()


def test_main_parses_question_and_show_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = AsyncMock()
    monkeypatch.setattr(main, "_run", run)
    monkeypatch.setattr("sys.argv", ["main", "--show-steps", "Design a platform"])
    main.main()
    run.assert_awaited_once_with("Design a platform", True)


def test_main_uses_default_question(monkeypatch: pytest.MonkeyPatch) -> None:
    run = AsyncMock()
    monkeypatch.setattr(main, "_run", run)
    monkeypatch.setattr("sys.argv", ["main"])
    main.main()
    run.assert_awaited_once_with(main.DEFAULT_QUESTION, False)
