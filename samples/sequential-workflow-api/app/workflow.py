# Copyright (c) Microsoft. All rights reserved.
"""Runs the 2-agent (researcher -> writer) sequential workflow per conversation thread.

Design goals (see samples/sequential-workflow-api/README.md for the "why"):
- A brand new `Workflow` (and brand new agent/chat-client instances) is built for every
  request. Nothing about a run is shared mutable state, so two different threads can
  execute the workflow fully concurrently.
- A lightweight, in-memory per-thread history + `asyncio.Lock` keeps a *single* thread's
  turns ordered and consistent, without blocking unrelated threads.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agent_framework import Agent, Message
from agent_framework.orchestrations import SequentialBuilder

from .agents import build_agents
from .config import Settings

logger = logging.getLogger(__name__)

AgentFactory = Callable[[], tuple[Agent, Agent]]


@dataclass
class ThreadState:
    """Conversation history and a lock scoped to a single thread/user."""

    history: list[Message] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class WorkflowRunResult:
    """Result of a single sequential workflow run."""

    thread_id: str
    prompt: str
    research_notes: str
    final_answer: str
    duration_seconds: float
    turn: int


class WorkflowService:
    """Coordinates concurrent, per-thread runs of the sequential workflow."""

    def __init__(self, agent_factory: AgentFactory) -> None:
        self._agent_factory = agent_factory
        self._threads: dict[str, ThreadState] = {}
        self._threads_guard = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "WorkflowService":
        return cls(agent_factory=lambda: build_agents(settings))

    async def _get_thread(self, thread_id: str) -> ThreadState:
        async with self._threads_guard:
            thread = self._threads.get(thread_id)
            if thread is None:
                thread = ThreadState()
                self._threads[thread_id] = thread
            return thread

    async def run(self, thread_id: str, prompt: str) -> WorkflowRunResult:
        """Run the researcher -> writer workflow for a single turn of `thread_id`."""
        thread = await self._get_thread(thread_id)
        async with thread.lock:
            start = time.perf_counter()
            turn = sum(1 for m in thread.history if m.role == "user") + 1
            logger.info("thread=%s turn=%s starting sequential workflow run", thread_id, turn)

            researcher, writer = self._agent_factory()
            workflow = SequentialBuilder(participants=[researcher, writer], output_from="all").build()

            conversation: list[Message | str] = [*thread.history, prompt]
            result = await workflow.run(conversation)
            outputs = result.get_outputs()
            if len(outputs) != 2:
                raise RuntimeError(f"Expected 2 workflow outputs (researcher, writer), got {len(outputs)}")
            research_response, writer_response = outputs

            new_messages: list[Message] = [Message(role="user", contents=[prompt])]
            new_messages.extend(research_response.messages)
            new_messages.extend(writer_response.messages)
            thread.history.extend(new_messages)

            duration = time.perf_counter() - start
            logger.info("thread=%s turn=%s finished in %.2fs", thread_id, turn, duration)

            return WorkflowRunResult(
                thread_id=thread_id,
                prompt=prompt,
                research_notes=research_response.text,
                final_answer=writer_response.text,
                duration_seconds=duration,
                turn=turn,
            )
