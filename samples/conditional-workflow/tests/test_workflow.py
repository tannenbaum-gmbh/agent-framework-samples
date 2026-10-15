from __future__ import annotations

import sys
from pathlib import Path

import pytest
from agent_framework import Agent, ChatResponse, Message

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.workflow import ContentWorkflow  # noqa: E402


class FakeChatClient:
    def __init__(self, name: str, calls: list[str], approve: bool) -> None:
        self.name = name
        self.calls = calls
        self.approve = approve

    async def get_response(self, messages: list[Message], **kwargs: object) -> ChatResponse:
        self.calls.append(self.name)
        responses = {
            "planner": "Audience: developers\nKey point: conditional workflows",
            "writer": "Initial draft",
            "reviewer": "APPROVED\nInitial draft" if self.approve else "REVISE\nAdd an example.",
            "reviser": "Revised draft with an example",
        }
        return ChatResponse(messages=[Message(role="assistant", contents=[responses[self.name]])])

    async def get_streaming_response(self, *args: object, **kwargs: object):  # pragma: no cover
        raise NotImplementedError


def make_agent_factory(calls: list[str], *, approve: bool):
    def factory() -> tuple[Agent, Agent, Agent, Agent]:
        return tuple(
            Agent(client=FakeChatClient(name, calls, approve), name=name)
            for name in ("planner", "writer", "reviewer", "reviser")
        )

    return factory


@pytest.mark.asyncio
async def test_approved_draft_skips_reviser() -> None:
    calls: list[str] = []
    workflow = ContentWorkflow(make_agent_factory(calls, approve=True))

    result = await workflow.run("Write a short explanation")

    assert result.final_content == "Initial draft"
    assert result.revised is False
    assert calls == ["planner", "writer", "reviewer"]


@pytest.mark.asyncio
async def test_rejected_draft_runs_reviser() -> None:
    calls: list[str] = []
    workflow = ContentWorkflow(make_agent_factory(calls, approve=False))

    result = await workflow.run("Write a short explanation")

    assert result.final_content == "Revised draft with an example"
    assert result.revised is True
    assert calls == ["planner", "writer", "reviewer", "reviser"]