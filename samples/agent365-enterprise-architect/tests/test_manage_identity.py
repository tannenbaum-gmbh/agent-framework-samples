# Copyright (c) Microsoft. All rights reserved.
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import manage_identity  # noqa: E402
from app.config import IdentitySettings  # noqa: E402

SETTINGS = IdentitySettings(tenant_id="tenant", blueprint_app_id="blueprint-app", agent_identity_id="agent-sp")


class FakeGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def me(self) -> dict[str, str]:
        return {"id": "me-id"}

    def create_agent_identity(self, *args: Any) -> dict[str, str]:
        self.calls.append(("create", args))
        return {"id": "agent-sp", "appId": "agent-app"}

    def set_agent_identity_enabled(self, *args: Any) -> None:
        self.calls.append(("enabled", args))

    def delete_agent_identity(self, *args: Any) -> None:
        self.calls.append(("delete", args))


def run(argv: list[str], graph: FakeGraph) -> None:
    args = manage_identity.build_parser().parse_args(argv)
    args.handler(args, SETTINGS, graph)


def test_identity_create_defaults_sponsor_to_signed_in_user(capsys: pytest.CaptureFixture[str]) -> None:
    graph = FakeGraph()
    run(["identity", "create", "--name", "Demo"], graph)
    assert graph.calls == [("create", ("blueprint-app", "Demo", "me-id"))]
    assert json.loads(capsys.readouterr().out) == {"agentIdentityId": "agent-sp", "agentIdentityClientId": "agent-app"}


def test_blueprint_auth_requires_explicit_sponsor() -> None:
    with pytest.raises(SystemExit, match="AGENT_SPONSOR_ID"):
        run(["identity", "create", "--name", "Demo", "--auth", "blueprint"], FakeGraph())


def test_disable_enable_and_delete_use_configured_identity() -> None:
    graph = FakeGraph()
    run(["identity", "disable"], graph)
    run(["identity", "enable", "--id", "other-sp"], graph)
    with pytest.raises(SystemExit, match="--yes"):
        run(["identity", "delete"], graph)
    run(["identity", "delete", "--yes"], graph)
    assert graph.calls == [
        ("enabled", ("agent-sp", False)),
        ("enabled", ("other-sp", True)),
        ("delete", ("agent-sp",)),
    ]


def test_blueprint_delete_requires_confirmation() -> None:
    with pytest.raises(SystemExit, match="--yes"):
        run(["blueprint", "delete"], FakeGraph())
