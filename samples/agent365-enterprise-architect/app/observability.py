# Copyright (c) Microsoft. All rights reserved.
"""S2S Activity tracing for the short-lived sandbox process."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator
from uuid import uuid4

from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential
from microsoft.opentelemetry import use_microsoft_opentelemetry
from microsoft.opentelemetry.a365.core import (
    AgentDetails,
    CallerDetails,
    InferenceCallDetails,
    InferenceOperationType,
    InferenceScope,
    InvokeAgentScope,
    InvokeAgentScopeDetails,
    Request,
    UserDetails,
)
from microsoft.opentelemetry.a365.core.middleware.baggage_builder import BaggageBuilder
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

if TYPE_CHECKING or __package__:
    from .agent_token import OBSERVABILITY_SCOPE
else:
    from agent_token import OBSERVABILITY_SCOPE

AGENT_NAME = "agent365-enterprise-architect"


class SandboxObservability:
    """Keep app-only export tokens fresh and flush spans before sandbox deletion."""

    def __init__(
        self,
        credential: AsyncTokenCredential,
        *,
        tenant_id: str,
        agent_id: str,
        blueprint_id: str,
        sponsor_id: str,
        sponsor_name: str,
        sponsor_email: str,
        capture_content: bool = False,
        caller_id: str | None = None,
        channel_name: str = "cli",
        conversation_id: str | None = None,
    ) -> None:
        if not all((tenant_id, agent_id, blueprint_id, sponsor_id, sponsor_name, sponsor_email)):
            raise ValueError("Observability requires tenant, agent, blueprint, and sponsor details")
        if agent_id == blueprint_id:
            raise ValueError("Observability must use the agent identity, not its blueprint")
        self._credential = credential
        self._tenant_id = tenant_id
        self._agent_id = agent_id
        self._blueprint_id = blueprint_id
        self._capture_content = capture_content
        self._caller_id = caller_id
        self._channel_name = channel_name
        self._token: AccessToken | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._provider: TracerProvider | None = None
        self._session_id = str(uuid4())
        self._conversation_id = conversation_id or self._session_id
        self.agent_details = AgentDetails(
            agent_id=agent_id,
            agent_name=AGENT_NAME,
            agent_blueprint_id=blueprint_id,
            tenant_id=tenant_id,
        )
        self.user_details = (
            UserDetails(user_id=caller_id)
            if caller_id is not None
            else UserDetails(user_id=sponsor_id, user_name=sponsor_name, user_email=sponsor_email)
        )
        self.caller_details = CallerDetails(user_details=self.user_details)

    def resolve_token(self, agent_id: str, tenant_id: str) -> str:
        if (agent_id, tenant_id) != (self._agent_id, self._tenant_id):
            raise ValueError("Exporter requested a token for an unexpected agent or tenant")
        token = self._token
        if token is None or token.expires_on <= time.time():
            raise RuntimeError("Agent 365 observability token is missing or expired")
        return token.token

    async def _refresh(self) -> None:
        self._token = await self._credential.get_token(OBSERVABILITY_SCOPE)
        if self._token.expires_on <= time.time():
            raise RuntimeError("Agent 365 observability token is already expired")

    async def _refresh_loop(self) -> None:
        while True:
            assert self._token is not None
            await asyncio.sleep(max(1, self._token.expires_on - time.time() - 300))
            await self._refresh()

    async def start(self) -> None:
        await self._refresh()
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("microsoft.opentelemetry").setLevel(os.environ.get("OTEL_LOG_LEVEL", "INFO").upper())
        # A365 auth mode: S2S; reuse sandbox federation, never a delegated telemetry token.
        use_microsoft_opentelemetry(
            enable_a365=True,
            a365_enable_observability_exporter=True,
            a365_use_s2s_endpoint=True,
            a365_token_resolver=self.resolve_token,
            a365_exporter_disable_offline_storage=True,
            enable_sensitive_data=self._capture_content,
            a365_suppress_invoke_agent_input=not self._capture_content,
            disable_logging=True,
            disable_metrics=True,
            resource=Resource.create({"service.name": AGENT_NAME}),
            # Manual invocation/inference scopes avoid duplicate framework/model spans.
            instrumentation_options={
                name: {"enabled": False}
                for name in ("agent_framework", "openai", "openai_agents", "langchain", "semantic_kernel")
            },
        )
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            raise RuntimeError("Microsoft OpenTelemetry did not initialize a tracer provider")
        self._provider = provider
        # Agent Framework also emits native spans independently of distro auto-instrumentation.
        from agent_framework.observability import disable_instrumentation

        disable_instrumentation()
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    def request(self, content: str) -> Request:
        return Request(
            content=content if self._capture_content else "[content omitted]",
            session_id=self._session_id,
            conversation_id=self._conversation_id,
        )

    @contextmanager
    def invocation(self, question: str) -> Generator[InvokeAgentScope]:
        with (
            BaggageBuilder()
            .tenant_id(self._tenant_id)
            .agent_id(self._agent_id)
            .agent_blueprint_id(self._blueprint_id)
            .session_id(self._session_id)
            .conversation_id(self._conversation_id)
            .channel_name(self._channel_name)
            .operation_source("HumanToAgent" if self._caller_id is not None else "EventToAgent")
            .user_id(self.user_details.user_id)
            .user_name(self.user_details.user_name)
            .user_email(self.user_details.user_email)
            .build()
        ):
            with InvokeAgentScope.start(
                self.request(question), InvokeAgentScopeDetails(), self.agent_details, self.caller_details
            ) as scope:
                yield scope

    def inference(self, question: str, model: str) -> InferenceScope:
        return InferenceScope.start(
            self.request(question),
            InferenceCallDetails(
                operationName=InferenceOperationType.CHAT, model=model, providerName="azure.ai.foundry"
            ),
            self.agent_details,
            self.user_details,
        )

    def record_output(self, scope: InvokeAgentScope | InferenceScope, answer: str) -> None:
        scope.record_output_messages([answer if self._capture_content else "[content omitted]"])

    async def close(self) -> None:
        try:
            if self._refresh_task is not None:
                self._refresh_task.cancel()
                try:
                    await self._refresh_task
                except asyncio.CancelledError:
                    pass
        finally:
            if self._provider is not None:
                try:
                    if not await asyncio.to_thread(self._provider.force_flush, 30_000):
                        raise RuntimeError("Timed out flushing Agent 365 telemetry")
                finally:
                    await asyncio.to_thread(self._provider.shutdown)
