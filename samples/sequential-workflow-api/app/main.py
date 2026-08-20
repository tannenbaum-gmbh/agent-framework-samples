# Copyright (c) Microsoft. All rights reserved.
"""FastAPI endpoint exposing the 2-agent sequential workflow.

Run locally with:
    uvicorn app.main:app --reload

Then, from two different terminals/clients, POST to /workflows/sequential/run with two
different `thread_id`s to see both requests execute concurrently (see README.md).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import get_settings
from .workflow import WorkflowService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.workflow_service = WorkflowService.from_settings(settings)
    logger.info("Workflow service ready (model deployment=%s)", settings.model_deployment)
    yield


app = FastAPI(
    title="Agent Framework - Sequential Workflow Sample",
    description=(
        "Exposes a 2-agent (researcher -> writer) Agent Framework sequential workflow "
        "as an HTTP endpoint, keyed by thread_id so multiple users/threads can run it "
        "concurrently."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


class RunWorkflowRequest(BaseModel):
    thread_id: str = Field(..., description="Identifier of the conversation thread/user.", min_length=1)
    message: str = Field(..., description="The user's message/question for this turn.", min_length=1)


class RunWorkflowResponse(BaseModel):
    thread_id: str
    turn: int
    prompt: str
    research_notes: str
    final_answer: str
    duration_seconds: float


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/workflows/sequential/run", response_model=RunWorkflowResponse)
async def run_sequential_workflow(request: RunWorkflowRequest) -> RunWorkflowResponse:
    """Run the sequential (researcher -> writer) workflow for one turn of a thread."""
    workflow_service: WorkflowService = app.state.workflow_service
    try:
        result = await workflow_service.run(thread_id=request.thread_id, prompt=request.message)
    except Exception as exc:  # noqa: BLE001 - surface as a 500 with a useful message
        logger.exception("Workflow run failed for thread_id=%s", request.thread_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return RunWorkflowResponse(
        thread_id=result.thread_id,
        turn=result.turn,
        prompt=result.prompt,
        research_notes=result.research_notes,
        final_answer=result.final_answer,
        duration_seconds=result.duration_seconds,
    )
