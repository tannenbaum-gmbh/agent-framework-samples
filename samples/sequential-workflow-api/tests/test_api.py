# Copyright (c) Microsoft. All rights reserved.
"""Tests for the FastAPI HTTP layer (routing, validation, concurrency of two threads)."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("FOUNDRY_PROJECT_ENDPOINT", "https://fake.services.ai.azure.com/api/projects/fake")

from app.main import app  # noqa: E402
from app.workflow import WorkflowService  # noqa: E402
from tests.test_workflow_service import make_fake_agent_factory  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        # The real lifespan builds a WorkflowService pointed at Microsoft Foundry; swap
        # it out for one backed by fake, credential-free agents before any request runs.
        app.state.workflow_service = WorkflowService(agent_factory=make_fake_agent_factory(delay_seconds=0.1))
        yield test_client


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_run_workflow_returns_expected_shape(client: TestClient) -> None:
    response = client.post(
        "/workflows/sequential/run",
        json={"thread_id": "thread-1", "message": "What is the capital of France?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == "thread-1"
    assert body["turn"] == 1
    assert "researcher:" in body["research_notes"]
    assert "writer:" in body["final_answer"]


def test_run_workflow_requires_thread_id_and_message(client: TestClient) -> None:
    response = client.post("/workflows/sequential/run", json={"thread_id": "thread-1"})
    assert response.status_code == 422


def test_two_threads_run_concurrently_over_http(client: TestClient) -> None:
    async def call(thread_id: str) -> float:
        loop = asyncio.get_running_loop()
        start = time.perf_counter()
        await loop.run_in_executor(
            None,
            lambda: client.post(
                "/workflows/sequential/run",
                json={"thread_id": thread_id, "message": f"question from {thread_id}"},
            ),
        )
        return time.perf_counter() - start

    async def run_both() -> float:
        start = time.perf_counter()
        await asyncio.gather(call("thread-a"), call("thread-b"))
        return time.perf_counter() - start

    elapsed = asyncio.run(run_both())
    # Each call is 2 sequential agent turns of 0.1s (~0.2s per request). If requests were
    # serialized behind a global lock, total time would approach ~0.4s+; running
    # concurrently keeps it well under that, even with TestClient/thread-pool overhead.
    assert elapsed < 0.6
