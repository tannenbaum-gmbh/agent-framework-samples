# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import run_agent  # noqa: E402


@pytest.mark.parametrize(
    ("mode", "token_file"),
    [("obo", None), ("s2s", "caller.token")],
)
def test_cli_rejects_mismatched_authentication_before_launching(
    monkeypatch: pytest.MonkeyPatch, mode: str, token_file: str | None,
) -> None:
    args = ["app.run_agent", "--version", "7"]
    if token_file:
        args += ["--user-token-file", token_file]
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(run_agent, "get_settings", lambda: SimpleNamespace(auth_mode=mode))
    with pytest.raises(SystemExit) as error:
        asyncio.run(run_agent.main())
    assert error.value.code == 2


def test_cli_reads_assertion_file_without_printing_or_deleting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    token_file = tmp_path / "caller.token"
    token_file.write_text("caller-access-token\n")
    monkeypatch.setattr(
        sys, "argv", ["app.run_agent", "--version", "7", "--user-token-file", str(token_file)]
    )
    monkeypatch.setattr(run_agent, "get_settings", lambda: SimpleNamespace(auth_mode="obo"))
    run = AsyncMock(return_value=SimpleNamespace(
        agent_identity="agent", blueprint="blueprint", user_agent="UA", answer="answer"
    ))
    monkeypatch.setattr(run_agent, "AgentIdentitySandboxAgent", lambda settings: SimpleNamespace(run=run))
    assert asyncio.run(run_agent.main()) == 0
    assert run.call_args.kwargs["user_assertion"] == "caller-access-token"
    output = capsys.readouterr()
    assert "caller-access-token" not in output.out + output.err
    assert token_file.exists()
