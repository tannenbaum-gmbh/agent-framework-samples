"""Adapters for the deployed architect and the Microsoft Learn A2A 1.0 agent."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import httpx
from a2a.types import Task, TaskState
from agent_framework import AgentResponse
from agent_framework.a2a import A2AAgent
from agent_framework_a2a import A2AContinuationToken
from azure.core.credentials_async import AsyncTokenCredential
from openai import AsyncOpenAI


class AzureA2AAuth(httpx.Auth):
    """Acquire a cached or refreshed Azure token for each SDK HTTP request."""

    def __init__(self, credential: AsyncTokenCredential) -> None:
        self._credential = credential

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self._credential.get_token("https://ai.azure.com/.default")
        request.headers["Authorization"] = f"Bearer {token.token}"
        yield request


class ArchitectAgent:
    def __init__(self, client: AsyncOpenAI, name: str) -> None:
        self._client = client
        self._name = name

    async def respond(self, prompt: str) -> str:
        response = await self._client.responses.create(
            input=prompt,
            tool_choice="required",
            extra_body={
                "agent_reference": {"name": self._name, "type": "agent_reference"}
            },
        )
        if response.status != "completed":
            raise RuntimeError(
                f"Architect response {response.status}: "
                f"{response.error or response.incomplete_details}"
            )
        if not response.output_text.strip():
            raise RuntimeError("Architect returned no text.")
        return response.output_text


class DocumentationAgent:
    """Apply workflow completion policy to the first-party remote agent."""

    def __init__(
        self,
        agent: A2AAgent,
        *,
        timeout: float = 120,
        poll_interval: float = 2,
    ) -> None:
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("A2A timeout and poll interval must be positive.")
        self._agent = agent
        self._timeout = timeout
        self._poll_interval = poll_interval

    async def respond(self, prompt: str) -> str:
        try:
            return await asyncio.wait_for(self._respond(prompt), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Microsoft Learn A2A review timed out after {self._timeout:g} seconds. "
                "The remote task may still be running."
            ) from exc

    async def _respond(self, prompt: str) -> str:
        response = await self._agent.run(prompt, background=True)
        task_id: str | None = None
        while True:
            text = self._review_text(response)
            raw = response.raw_representation
            items = raw if isinstance(raw, list) else [raw]
            for item in items:
                if isinstance(item, Task):
                    if not item.id:
                        raise RuntimeError("A2A task has no ID.")
                    if task_id is not None and item.id != task_id:
                        raise RuntimeError("A2A polling returned a different task ID.")
                    task_id = item.id
            token = response.continuation_token
            if token is None:
                if not text.strip():
                    raise RuntimeError("Microsoft Learn A2A agent returned no text.")
                return text
            poll_task_id = token.get("task_id")
            context_id = token.get("context_id")
            if not isinstance(poll_task_id, str) or not isinstance(context_id, str):
                raise RuntimeError("Microsoft Learn returned an invalid A2A continuation token.")
            await asyncio.sleep(self._poll_interval)
            response = await self._agent.poll_task(
                A2AContinuationToken(task_id=poll_task_id, context_id=context_id)
            )

    @staticmethod
    def _review_text(response: AgentResponse) -> str:
        raw = response.raw_representation
        items = raw if isinstance(raw, list) else [raw]
        status_text: list[str] = []
        for item in items:
            if not isinstance(item, Task):
                continue
            if item.status.state not in (
                TaskState.TASK_STATE_SUBMITTED,
                TaskState.TASK_STATE_WORKING,
                TaskState.TASK_STATE_COMPLETED,
            ):
                raise RuntimeError(
                    f"Microsoft Learn A2A task {item.id} stopped in "
                    f"{TaskState.Name(item.status.state)}: {item.status}"
                )
            if (
                item.status.state != TaskState.TASK_STATE_COMPLETED
                and response.continuation_token is None
            ):
                raise RuntimeError("Microsoft Learn A2A task is incomplete without a continuation token.")
            if item.status.state == TaskState.TASK_STATE_COMPLETED:
                status_text.extend(part.text for part in item.status.message.parts if part.text)
        text = "\n".join(
            content.text
            for message in response.messages
            for content in message.contents
            if content.type == "text" and content.text
        )
        return text or "\n".join(status_text)
