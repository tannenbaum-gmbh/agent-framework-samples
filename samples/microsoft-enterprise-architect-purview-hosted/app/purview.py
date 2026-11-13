"""Purview SDK configuration for a single-user hosted workshop deployment."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from copy import copy

from agent_framework import (
    ChatContext,
    ChatMiddleware,
    ChatResponse,
    ChatResponseUpdate,
    MiddlewareTermination,
    ResponseStream,
)
from agent_framework.microsoft import (
    PurviewAppLocation,
    PurviewChatPolicyMiddleware,
    PurviewLocationType,
    PurviewSettings,
)
from azure.core.credentials import TokenCredential

from .config import Settings


class WorkshopUserMiddleware(ChatMiddleware):
    """Bind app-only policy checks to a server-configured user, never message data."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    async def process(
        self, context: ChatContext, call_next: Callable[[], Awaitable[None]],
    ) -> None:
        messages = []
        for original in context.messages:
            message = copy(original)
            message.author_name = self.user_id
            message.additional_properties = {
                **(original.additional_properties or {}), "user_id": self.user_id,
            }
            messages.append(message)
        context.messages = messages
        await call_next()


class HostedPurviewChatPolicyMiddleware(PurviewChatPolicyMiddleware):
    async def process(
        self, context: ChatContext, call_next: Callable[[], Awaitable[None]],
    ) -> None:
        try:
            await super().process(context, call_next)
        except MiddlewareTermination:
            # Preview SDK returns ChatResponse for a blocked prompt even in stream mode.
            if context.stream and isinstance(context.result, ChatResponse):
                response = context.result

                async def updates() -> AsyncIterator[ChatResponseUpdate]:
                    for update in response.to_updates():
                        yield update

                context.result = ResponseStream(updates(), finalizer=lambda _: response)
            raise


def create_purview_middleware(
    settings: Settings, credential: TokenCredential,
) -> list[ChatMiddleware]:
    if not all((settings.purview_tenant_id, settings.purview_app_id, settings.purview_user_id)):
        raise ValueError("PURVIEW_TENANT_ID, PURVIEW_APP_ID, and PURVIEW_USER_ID are required.")
    policy_settings: PurviewSettings = {
        "app_name": "Microsoft Enterprise Architect Purview Hosted",
        "tenant_id": settings.purview_tenant_id,
        "purview_app_location": PurviewAppLocation(
            location_type=PurviewLocationType.APPLICATION,
            location_value=settings.purview_app_id,
        ),
        "ignore_exceptions": False,
        "ignore_payment_required": False,
    }
    return [
        WorkshopUserMiddleware(settings.purview_user_id),
        HostedPurviewChatPolicyMiddleware(credential=credential, settings=policy_settings),
    ]
