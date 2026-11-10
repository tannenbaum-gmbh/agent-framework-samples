# Copyright (c) Microsoft. All rights reserved.
"""Invoke the versioned prompt agent from the sandbox as an Entra agent identity."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from microsoft_agents.authentication.msal import MsalAuth
from microsoft_agents.hosting.core import AgentAuthConfiguration, AuthTypes
from microsoft_agents_a365.runtime import Utility
from msal import ConfidentialClientApplication, TokenCache

if __package__:
    from .agent_token import OBSERVABILITY_SCOPE, AgentIdentityTokenProvider, managed_identity_assertion
else:
    from agent_token import OBSERVABILITY_SCOPE, AgentIdentityTokenProvider, managed_identity_assertion

FOUNDRY_SCOPE = "https://ai.azure.com/.default"

if TYPE_CHECKING:
    from .observability import SandboxObservability


class AgentIdentityCredential(AsyncTokenCredential):
    """Acquire agent tokens using the Agents SDK and managed-identity federation."""

    def __init__(
        self, tenant_id: str, blueprint_app_id: str, agent_client_id: str, managed_identity_client_id: str | None
    ) -> None:
        if not all((tenant_id, blueprint_app_id, agent_client_id)):
            raise ValueError("Tenant, blueprint, and agent client IDs are required")
        self._tenant_id = tenant_id
        self._agent_client_id = agent_client_id
        # The SDK's FederatedCredentials mode only selects user-assigned identities.
        self._system_identity_provider = (
            AgentIdentityTokenProvider(
                tenant_id, blueprint_app_id, assertion_provider=managed_identity_assertion(None)
            )
            if not managed_identity_client_id
            else None
        )
        self._connection = (
            MsalAuth(
                AgentAuthConfiguration(
                    tenant_id=tenant_id,
                    client_id=blueprint_app_id,
                    auth_type=AuthTypes.federated_credentials,
                    federated_client_id=managed_identity_client_id,
                )
            )
            if managed_identity_client_id
            else None
        )
        self._cache = TokenCache()

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        if len(scopes) != 1 or scopes[0] not in {FOUNDRY_SCOPE, OBSERVABILITY_SCOPE}:
            raise ValueError(f"This credential only supports {FOUNDRY_SCOPE} or {OBSERVABILITY_SCOPE}")
        if self._system_identity_provider is not None:
            token, expires_on = await asyncio.to_thread(
                self._system_identity_provider.agent_identity_token, self._agent_client_id, scopes[0]
            )
            return AccessToken(token, expires_on)
        assert self._connection is not None
        assertion = await self._connection.get_agentic_application_token(self._tenant_id, self._agent_client_id)
        if not assertion:
            raise RuntimeError("Agents SDK blueprint token exchange failed: no access token returned")

        def acquire() -> AccessToken:
            app = ConfidentialClientApplication(
                self._agent_client_id,
                authority=f"https://login.microsoftonline.com/{self._tenant_id}",
                client_credential={"client_assertion": assertion},
                token_cache=self._cache,
            )
            result = app.acquire_token_for_client(list(scopes))
            if "access_token" not in result:
                detail = result.get("error_description") or result.get("error") or "unknown error"
                raise RuntimeError(f"Agent identity token request failed: {detail}")
            expires_on = int(time.time()) + int(result["expires_in"])
            return AccessToken(result["access_token"], expires_on)

        return await asyncio.to_thread(acquire)

    async def close(self) -> None:
        """No asynchronous transport is owned by this credential."""


class AgentIdentityAccessTokenCredential:
    """Adapt the already-acquired identity token to Azure SDK's sync credential protocol."""

    def __init__(self, token: AccessToken) -> None:
        self._token = token

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        if scopes != (FOUNDRY_SCOPE,):
            raise ValueError(f"This credential only supports {FOUNDRY_SCOPE}")
        return self._token


def invoke_prompt_agent(request: dict[str, Any], token: AccessToken) -> str:
    """Call the versioned prompt agent as the Agent 365 identity."""
    from azure.ai.projects import AIProjectClient

    credential = AgentIdentityAccessTokenCredential(token)
    with AIProjectClient(endpoint=request["project_endpoint"], credential=credential) as project:
        with project.get_openai_client() as client:
            conversation = client.conversations.create()
            try:
                response = client.responses.create(
                    conversation=conversation.id,
                    input=request["question"],
                    tool_choice="auto",
                    extra_body={
                        "agent_reference": {
                            "name": request["agent_name"],
                            "version": request["agent_version"],
                            "type": "agent_reference",
                        }
                    },
                )
                if response.status != "completed":
                    raise RuntimeError(
                        f"Prompt agent response {response.status}: "
                        f"{response.error or response.incomplete_details}"
                    )
                for item in response.output:
                    if item.type == "mcp_call" and item.error:
                        raise RuntimeError(f"MCP tool error: {item.error}")
                    if item.type == "mcp_approval_request":
                        raise RuntimeError(
                            "Unexpected MCP approval request. Create a new prompt-agent version "
                            'with require_approval="never".'
                        )
                return response.output_text
            finally:
                client.conversations.delete(conversation_id=conversation.id)


def describe_identity(token: str, expected_agent_identity: str) -> dict[str, str]:
    """Resolve which Agent 365 identity this process runs as.

    The Agent 365 SDK reads the agent identity (``appid``/``azp``) and the parent blueprint
    (``xms_par_app_azp``) from the token. Signatures are not verified here, so this is a
    diagnostic check that catches misconfiguration, not an authorization decision.
    """
    agent_identity = Utility.get_app_id_from_token(token)
    blueprint = Utility.get_agent_id_from_token(token)
    if agent_identity != expected_agent_identity:
        raise RuntimeError(
            f"Token was issued to {agent_identity or 'an unknown client'}, expected agent identity "
            f"{expected_agent_identity}"
        )
    return {
        "agent_identity": agent_identity,
        "blueprint": blueprint,
        "user_agent": Utility.get_user_agent_header("Agent365EnterpriseArchitect"),
    }


async def run(request_path: Path) -> dict[str, Any]:
    """Run one request through an agent that authenticates as its own agent identity."""
    request = json.loads(request_path.read_text(encoding="utf-8"))
    credential = AgentIdentityCredential(
        request["tenant_id"],
        request["blueprint_app_id"],
        request["agent_identity_client_id"],
        request.get("managed_identity_client_id"),
    )
    telemetry: SandboxObservability | None = None
    try:
        token = await credential.get_token(FOUNDRY_SCOPE)
        identity = describe_identity(token.token, request["agent_identity_client_id"])
        if identity["blueprint"] != request["blueprint_app_id"]:
            raise RuntimeError(f"Token blueprint does not match expected blueprint {request['blueprint_app_id']}")

        if request.get("observability_enabled", False):
            for name in ("OTEL_LOG_LEVEL", "A365_OBSERVABILITY_LOG_LEVEL"):
                if name in request:
                    os.environ[name] = request[name]
            if __package__:
                from .observability import SandboxObservability
            else:
                from observability import SandboxObservability
            telemetry = SandboxObservability(
                credential,
                tenant_id=request["tenant_id"],
                agent_id=identity["agent_identity"],
                blueprint_id=identity["blueprint"],
                sponsor_id=request["observability_sponsor_id"],
                sponsor_name=request["observability_sponsor_name"],
                sponsor_email=request["observability_sponsor_email"],
                capture_content=request.get("observability_capture_content", False),
            )
            await telemetry.start()

        if telemetry is None:
            answer = await asyncio.to_thread(invoke_prompt_agent, request, token)
        else:
            with telemetry.invocation(request["question"]) as invocation:
                with telemetry.inference(request["question"], request["model_deployment"]) as inference:
                    answer = await asyncio.to_thread(invoke_prompt_agent, request, token)
                    telemetry.record_output(inference, answer)
                telemetry.record_output(invocation, answer)
        return {**identity, "answer": answer}
    finally:
        try:
            if telemetry is not None:
                await telemetry.close()
        finally:
            await credential.close()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(await run(args.request)))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
