from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from a2a.client.errors import A2AClientError
from agent_framework.a2a import A2AAgent

from app.agents import ArchitectAgent, AzureA2AAuth, DocumentationAgent

ENDPOINT = "https://example.services.ai.azure.com/api/projects/demo/agents/docs/endpoint/protocols/a2a"


def task(state: str, **kwargs: object) -> dict[str, object]:
    return {"id": "task-1", "status": {"state": state}, **kwargs}


def completed() -> dict[str, object]:
    return task(
        "TASK_STATE_COMPLETED",
        artifacts=[
            {"parts": [{"text": "Verified claim"}, {"text": "https://learn.microsoft.com/"}]}
        ],
    )


async def invoke_a2a(
    results: list[dict[str, object]], *, error: bool = False
) -> tuple[str, list[dict], AsyncMock]:
    requests: list[dict] = []
    credential = AsyncMock()
    credential.get_token.return_value = SimpleNamespace(token="test-token")

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        assert str(request.url) == ENDPOINT
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["A2A-Version"] == "1.0"
        result = results.pop(0)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error" if error else "result": result,
            },
        )

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(handle), auth=AzureA2AAuth(credential)
        ) as http,
        A2AAgent(name="docs", url=ENDPOINT, http_client=http) as remote,
    ):
        text = await DocumentationAgent(remote, poll_interval=0.001).respond(
            "Review this architecture"
        )
    return text, requests, credential


async def test_a2a_immediate_message_uses_ga_protocol_and_azure_token() -> None:
    text, requests, credential = await invoke_a2a(
        [{"message": {"parts": [{"text": "Documentation answer"}]}}]
    )
    assert text == "Documentation answer"
    assert requests[0]["method"] == "SendMessage"
    message = requests[0]["params"]["message"]
    assert message["role"] == "ROLE_USER"
    assert message["parts"][0]["text"] == "Review this architecture"
    assert message["messageId"]
    assert requests[0]["params"]["configuration"]["returnImmediately"] is True
    credential.get_token.assert_awaited_once_with("https://ai.azure.com/.default")


async def test_a2a_completed_task_preserves_all_text_and_urls() -> None:
    text, requests, _ = await invoke_a2a([{"task": completed()}])
    assert "Verified claim" in text
    assert "https://learn.microsoft.com/" in text
    assert len(requests) == 1


async def test_a2a_polls_submitted_and_working_tasks() -> None:
    text, requests, credential = await invoke_a2a(
        [{"task": task("TASK_STATE_SUBMITTED")}, task("TASK_STATE_WORKING"), completed()]
    )
    assert text.startswith("Verified claim")
    assert [request["method"] for request in requests] == [
        "SendMessage", "GetTask", "GetTask"
    ]
    assert requests[1]["params"] == {"id": "task-1"}
    assert len({request["id"] for request in requests}) == 3
    assert credential.get_token.await_count == 3


async def test_a2a_completed_status_message() -> None:
    text, _, _ = await invoke_a2a(
        [{"task": {
            "id": "task-1",
            "status": {
                "state": "TASK_STATE_COMPLETED",
                "message": {"parts": [{"text": "Status answer"}]},
            },
        }}]
    )
    assert text == "Status answer"


@pytest.mark.parametrize(
    "state",
    [
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_REJECTED",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_AUTH_REQUIRED",
    ],
)
async def test_a2a_non_completed_task_is_an_error(state: str) -> None:
    with pytest.raises(RuntimeError, match=state):
        await invoke_a2a([{"task": task(state)}])


async def test_a2a_authorization_message_is_preserved_in_error() -> None:
    with pytest.raises(RuntimeError, match="https://example.com/consent"):
        await invoke_a2a(
            [{"task": {
                "id": "task-1",
                "status": {
                    "state": "TASK_STATE_AUTH_REQUIRED",
                    "message": {"parts": [{"text": "https://example.com/consent"}]},
                },
            }}]
        )


async def test_a2a_rpc_error_is_not_treated_as_text() -> None:
    with pytest.raises(A2AClientError, match="permission denied"):
        await invoke_a2a([{"code": -32000, "message": "permission denied"}], error=True)


@pytest.mark.parametrize(
    "result, error",
    [
        ({"message": {"parts": []}}, "returned no text"),
        ({"task": {"status": {"state": "TASK_STATE_COMPLETED"}}}, "no ID"),
        ({"task": task("TASK_STATE_COMPLETED")}, "returned no text"),
        ({"task": task("TASK_STATE_UNSPECIFIED")}, "returned no text"),
    ],
)
async def test_a2a_rejects_malformed_or_empty_results(
    result: dict[str, object], error: str
) -> None:
    with pytest.raises(RuntimeError, match=error):
        await invoke_a2a([result])


async def test_a2a_rejects_mismatched_task() -> None:
    with pytest.raises(RuntimeError, match="different task ID"):
        await invoke_a2a(
            [
                {"task": task("TASK_STATE_WORKING")},
                {**completed(), "id": "another-task"},
            ]
        )


async def test_a2a_timeout_includes_polling() -> None:
    credential = AsyncMock()
    credential.get_token.return_value = SimpleNamespace(token="test-token")

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return httpx.Response(
            200, json={
                "jsonrpc": "2.0", "id": payload["id"],
                "result": {"task": task("TASK_STATE_WORKING")},
            }
        )

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(handle), auth=AzureA2AAuth(credential)
        ) as http,
        A2AAgent(name="docs", url=ENDPOINT, http_client=http) as remote,
    ):
        agent = DocumentationAgent(remote, timeout=0.01)
        with pytest.raises(TimeoutError, match="remote task may still be running"):
            await agent.respond("Review")


@pytest.mark.parametrize("status", [401, 403, 500])
async def test_a2a_http_errors_propagate(status: int) -> None:
    credential = AsyncMock()
    credential.get_token.return_value = SimpleNamespace(token="test-token")
    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(status)),
            auth=AzureA2AAuth(credential),
        ) as http,
        A2AAgent(name="docs", url=ENDPOINT, http_client=http) as remote,
    ):
        with pytest.raises(A2AClientError, match=str(status)):
            await DocumentationAgent(remote).respond("Review")


@pytest.mark.parametrize("field", ["parts", "artifacts"])
async def test_sdk_rejects_malformed_protocol_fields(field: str) -> None:
    from google.protobuf.json_format import ParseError

    result = (
        {"message": {"parts": "invalid"}}
        if field == "parts"
        else {"task": task("TASK_STATE_COMPLETED", artifacts="invalid")}
    )
    with pytest.raises(ParseError):
        await invoke_a2a([result])


async def test_sdk_rejects_missing_message_and_task() -> None:
    with pytest.raises(ValueError, match="neither task nor message"):
        await invoke_a2a([{}])


async def test_failed_task_artifacts_are_not_successful_review_text() -> None:
    failed = completed()
    failed["status"] = {"state": "TASK_STATE_FAILED"}
    with pytest.raises(RuntimeError, match="TASK_STATE_FAILED"):
        await invoke_a2a([{"task": failed}])


async def test_a2a_preserves_multiple_artifacts() -> None:
    result = completed()
    result["artifacts"].append({"parts": [{"text": "Another finding"}]})
    text, _, _ = await invoke_a2a([{"task": result}])
    assert "Verified claim" in text
    assert "https://learn.microsoft.com/" in text
    assert "Another finding" in text


async def test_native_agent_does_not_reuse_session_and_http_client_is_caller_owned() -> None:
    credential = AsyncMock()
    credential.get_token.side_effect = [
        SimpleNamespace(token="first-token"),
        SimpleNamespace(token="refreshed-token"),
    ]
    requests: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        expected_token = "first-token" if len(requests) == 1 else "refreshed-token"
        assert request.headers["Authorization"] == f"Bearer {expected_token}"
        message = payload["params"]["message"]
        assert not message.get("contextId")
        assert not message.get("taskId")
        assert not message.get("referenceTaskIds")
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0", "id": payload["id"],
                "result": {
                    "message": {
                        "messageId": "answer",
                        "contextId": f"context-{len(requests)}",
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "Answer"}],
                    }
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), auth=AzureA2AAuth(credential)
    ) as http:
        async with A2AAgent(name="docs", url=ENDPOINT, http_client=http) as remote:
            agent = DocumentationAgent(remote)
            assert await agent.respond("First workload") == "Answer"
            assert await agent.respond("Second workload") == "Answer"
        assert not http.is_closed
    assert http.is_closed
    assert len(requests) == 2
    assert credential.get_token.await_count == 2


async def test_a2a_timeout_includes_initial_sdk_request() -> None:
    credential = AsyncMock()
    credential.get_token.return_value = SimpleNamespace(token="test-token")

    async def handle(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(500)

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(handle), auth=AzureA2AAuth(credential)
        ) as http,
        A2AAgent(name="docs", url=ENDPOINT, http_client=http) as remote,
    ):
        with pytest.raises(TimeoutError, match="remote task may still be running"):
            await DocumentationAgent(remote, timeout=0.01).respond("Review")


async def test_credential_failure_prevents_remote_request() -> None:
    credential = AsyncMock()
    credential.get_token.side_effect = RuntimeError("Azure sign-in required")

    def unexpected(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Unauthenticated request must not be sent")

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(unexpected), auth=AzureA2AAuth(credential)
        ) as http,
        A2AAgent(name="docs", url=ENDPOINT, http_client=http) as remote,
    ):
        with pytest.raises(RuntimeError, match="Azure sign-in required"):
            await DocumentationAgent(remote).respond("Review")


async def test_architect_references_existing_agent_and_requires_tools() -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(
        return_value=SimpleNamespace(status="completed", output_text="Design")
    )
    assert await ArchitectAgent(client, "custom-architect").respond("Design this") == "Design"
    client.responses.create.assert_awaited_once_with(
        input="Design this",
        tool_choice="required",
        extra_body={
            "agent_reference": {"name": "custom-architect", "type": "agent_reference"}
        },
    )
    assert "previous_response_id" not in client.responses.create.call_args.kwargs


@pytest.mark.parametrize("status", ["failed", "incomplete", "in_progress"])
async def test_architect_failure_is_explicit(status: str) -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(
        return_value=SimpleNamespace(
            status=status, error="tool failure", incomplete_details=None
        )
    )
    with pytest.raises(RuntimeError, match=f"Architect response {status}: tool failure"):
        await ArchitectAgent(client, "architect").respond("Design")


async def test_architect_empty_text_is_an_error() -> None:
    client = MagicMock()
    client.responses.create = AsyncMock(
        return_value=SimpleNamespace(status="completed", output_text=" ")
    )
    with pytest.raises(RuntimeError, match="returned no text"):
        await ArchitectAgent(client, "architect").respond("Design")
