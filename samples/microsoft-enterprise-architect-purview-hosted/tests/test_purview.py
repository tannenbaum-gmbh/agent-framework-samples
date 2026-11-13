import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
import httpx
from agent_framework import ChatContext, ChatResponse, Message, MiddlewareTermination
from agent_framework.microsoft import PurviewAuthenticationError, PurviewPaymentRequiredError

from app.config import Settings, get_settings
from app.purview import create_purview_middleware

SETTINGS = Settings(
    "https://example.services.ai.azure.com/api/projects/demo", "workshop-model",
    purview_tenant_id="11111111-1111-1111-1111-111111111111",
    purview_app_id="22222222-2222-2222-2222-222222222222",
    purview_user_id="33333333-3333-3333-3333-333333333333",
)


@pytest.mark.parametrize("name", ["PURVIEW_TENANT_ID", "PURVIEW_APP_ID", "PURVIEW_USER_ID"])
@pytest.mark.parametrize("value", [None, "", "not-an-id"])
def test_configuration_rejects_missing_or_invalid_purview_ids(monkeypatch, name, value):
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", SETTINGS.project_endpoint)
    for key, field in (
        ("PURVIEW_TENANT_ID", "purview_tenant_id"),
        ("PURVIEW_APP_ID", "purview_app_id"),
        ("PURVIEW_USER_ID", "purview_user_id"),
    ):
        monkeypatch.setenv(key, getattr(SETTINGS, field))
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        get_settings()


def test_factory_rejects_unconfigured_identity(purview_credential):
    with pytest.raises(ValueError, match="PURVIEW_USER_ID"):
        create_purview_middleware(replace(SETTINGS, purview_user_id=""), purview_credential)


@pytest.mark.parametrize("block_activity", [None, "uploadText", "downloadText"])
def test_sdk_enforces_policy_and_trusted_identity(purview_graph, purview_credential, block_activity):
    purview_graph["block_activity"] = block_activity
    binding, policy = create_purview_middleware(SETTINGS, purview_credential)
    original = Message(
        role="user", contents=["My credit card is 4111 1111 1111 1111"],
        author_name="44444444-4444-4444-4444-444444444444",
        additional_properties={"user_id": "55555555-5555-5555-5555-555555555555"},
    )
    context = ChatContext(client=MagicMock(), messages=[original], options={})

    async def model():
        context.result = ChatResponse(messages=[Message(role="assistant", contents=["Model response"])])

    model_call = AsyncMock(side_effect=model)

    async def scenario():
        try:
            await binding.process(context, lambda: policy.process(context, model_call))
        except MiddlewareTermination:
            assert block_activity == "uploadText"
        finally:
            await asyncio.sleep(0)
            await policy._client.close()

    asyncio.run(scenario())
    if block_activity == "uploadText":
        model_call.assert_not_called()
        assert context.result.text == "Prompt blocked by policy"
    else:
        model_call.assert_awaited_once()
        expected = "Response blocked by policy" if block_activity else "Model response"
        assert context.result.text == expected

    assert original.author_name == "44444444-4444-4444-4444-444444444444"
    assert original.additional_properties["user_id"] == "55555555-5555-5555-5555-555555555555"
    evaluated = [
        request for request in purview_graph["requests"]
        if request.url.path.endswith("/processContent")
    ]
    assert len(evaluated) == (1 if block_activity == "uploadText" else 2)
    for request in evaluated:
        assert f"/users/{SETTINGS.purview_user_id}/" in request.url.path
        payload = json.loads(request.content)
        assert payload["contentToProcess"]["protectedAppMetadata"]["applicationLocation"]["value"] == SETTINGS.purview_app_id


@pytest.mark.parametrize(("status", "error"), [
    (403, PurviewAuthenticationError), (402, PurviewPaymentRequiredError),
    (None, httpx.ReadTimeout),
])
def test_sdk_errors_fail_closed_before_model(purview_graph, purview_credential, status, error):
    if status is None:
        purview_graph["timeout"] = True
    else:
        purview_graph["status"] = status
    binding, policy = create_purview_middleware(SETTINGS, purview_credential)
    context = ChatContext(
        client=MagicMock(), messages=[Message(role="user", contents=["Ordinary prompt"])], options={},
    )
    model_call = AsyncMock()

    async def scenario():
        try:
            with pytest.raises(error):
                await binding.process(context, lambda: policy.process(context, model_call))
        finally:
            await asyncio.sleep(0)
            await policy._client.close()

    asyncio.run(scenario())
    model_call.assert_not_called()
