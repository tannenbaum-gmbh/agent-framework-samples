# Copyright (c) Microsoft. All rights reserved.
"""Tests for the sequential workflow FastAPI sample.

These tests use a fake in-memory chat client so they run without any Azure/Foundry
credentials or network access, while still exercising the real Agent Framework
`SequentialBuilder` workflow and the FastAPI endpoint end to end.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest
from agent_framework import Agent, ChatResponse, Message

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.workflow import WorkflowService  # noqa: E402


class FakeChatClient:
    """A minimal chat client that echoes deterministic, delayed responses.

    The artificial delay lets tests assert that two different threads actually run
    concurrently rather than being serialized.
    """

    def __init__(self, name: str, delay_seconds: float = 0.05) -> None:
        self.name = name
        self.delay_seconds = delay_seconds
        self.call_count = 0

    async def get_response(self, messages: list[Message], **kwargs: object) -> ChatResponse:
        self.call_count += 1
        await asyncio.sleep(self.delay_seconds)
        last_user_message = next((m.text for m in reversed(messages) if m.role == "user"), "")
        reply = Message(role="assistant", contents=[f"{self.name}: {last_user_message}"])
        return ChatResponse(messages=[reply])

    async def get_streaming_response(self, *args: object, **kwargs: object):  # pragma: no cover - unused
        raise NotImplementedError


def make_fake_agent_factory(delay_seconds: float = 0.05):
    def factory() -> tuple[Agent, Agent]:
        researcher = Agent(client=FakeChatClient("researcher", delay_seconds), name="researcher")
        writer = Agent(client=FakeChatClient("writer", delay_seconds), name="writer")
        return researcher, writer

    return factory


@pytest.mark.asyncio
async def test_single_run_returns_researcher_and_writer_output() -> None:
    service = WorkflowService(agent_factory=make_fake_agent_factory())

    result = await service.run(thread_id="thread-1", prompt="What is the capital of France?")

    assert result.thread_id == "thread-1"
    assert result.turn == 1
    assert "researcher:" in result.research_notes
    assert "writer:" in result.final_answer


@pytest.mark.asyncio
async def test_same_thread_runs_maintain_conversation_history() -> None:
    service = WorkflowService(agent_factory=make_fake_agent_factory())

    first = await service.run(thread_id="thread-1", prompt="first question")
    second = await service.run(thread_id="thread-1", prompt="second question")

    assert first.turn == 1
    assert second.turn == 2
    thread = service._threads["thread-1"]  # noqa: SLF001 - inspecting internal state in a test
    # user + researcher + writer messages per turn
    assert len(thread.history) == 6


@pytest.mark.asyncio
async def test_two_different_threads_run_concurrently() -> None:
    """Two different threads should overlap in wall-clock time, proving no global lock."""
    delay = 0.2
    service = WorkflowService(agent_factory=make_fake_agent_factory(delay_seconds=delay))

    start = time.perf_counter()
    results = await asyncio.gather(
        service.run(thread_id="thread-a", prompt="question A"),
        service.run(thread_id="thread-b", prompt="question B"),
    )
    elapsed = time.perf_counter() - start

    assert {r.thread_id for r in results} == {"thread-a", "thread-b"}
    # Each run makes 2 sequential agent calls of `delay` seconds each (~2*delay).
    # If the two threads were serialized we would expect ~4*delay; allow generous slack.
    assert elapsed < delay * 3.5


@pytest.mark.asyncio
async def test_same_thread_calls_are_serialized() -> None:
    """Two concurrent calls for the *same* thread should not interleave (lock scoping)."""
    delay = 0.1
    service = WorkflowService(agent_factory=make_fake_agent_factory(delay_seconds=delay))

    start = time.perf_counter()
    await asyncio.gather(
        service.run(thread_id="thread-a", prompt="first"),
        service.run(thread_id="thread-a", prompt="second"),
    )
    elapsed = time.perf_counter() - start

    # Two turns of 2 sequential agent calls each, fully serialized: ~4*delay.
    assert elapsed >= delay * 3.5
