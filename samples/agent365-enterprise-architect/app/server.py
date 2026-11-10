# Copyright (c) Microsoft. All rights reserved.
"""Authenticated Microsoft 365 Agents SDK Activity endpoint for local development."""

from __future__ import annotations

import logging
import os

from aiohttp import web
from microsoft_agents.activity import ActivityTypes, load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import CloudAdapter, start_agent_process
from microsoft_agents.hosting.aiohttp.jwt_authorization_middleware import jwt_authorization_decorator
from microsoft_agents.hosting.core import (
    AgentApplication,
    AgentAuthConfiguration,
    ApplicationOptions,
    AuthHandler,
    Authorization,
    MemoryStorage,
    TurnContext,
    TurnState,
)

from .agent import AGENT_NAME
from .config import Settings, get_settings
from .host import AgentIdentitySandboxAgent

AUTH_HANDLER = "CALLER"
AGENT_APP_KEY = web.AppKey("agent_app", AgentApplication)


class EnterpriseArchitectApplication(AgentApplication[TurnState]):
    def __init__(
        self, options: ApplicationOptions, authorization: Authorization, settings: Settings, version: str
    ) -> None:
        super().__init__(options, authorization=authorization)
        self._settings = settings
        self._version = version
        self.activity(ActivityTypes.message, auth_handlers=[AUTH_HANDLER])(self.on_message)
        self.error(self.on_error)
        self.on_sign_in_failure(self.sign_in_failure)

    async def on_message(self, context: TurnContext, _state: TurnState) -> None:
        question = context.activity.text
        if not question or not question.strip():
            await context.send_activity("Send a text architecture question to continue.")
            return
        token = await self.auth.get_token(context, AUTH_HANDLER)
        if not token or not token.token:
            raise RuntimeError("Caller authorization completed without an access token")
        if not context.activity.conversation or not context.activity.conversation.id:
            raise ValueError("A conversation ID is required")
        if not context.activity.channel_id:
            raise ValueError("A channel ID is required")
        result = await AgentIdentitySandboxAgent(self._settings).run(
            question,
            agent_name=AGENT_NAME,
            version=self._version,
            user_assertion=token.token,
            channel_name=str(context.activity.channel_id),
            conversation_id=context.activity.conversation.id,
        )
        await context.send_activity(result.answer)

    async def on_error(self, context: TurnContext, _error: Exception) -> None:
        # AgentApplication logs the exception before invoking this notification handler.
        await context.send_activity("The request failed. Check the agent host logs before retrying.")

    async def sign_in_failure(self, context: TurnContext, _state: TurnState, _handler: str | None) -> None:
        logging.getLogger(__name__).warning("Caller sign-in failed")
        await context.send_activity("Sign-in failed. Please sign in again before sending another question.")


def create_app(
    settings: Settings | None = None,
    *,
    channel_auth: AgentAuthConfiguration | None = None,
    oauth_connection_name: str | None = None,
    version: str | None = None,
) -> web.Application:
    settings = settings or get_settings()
    if settings.auth_mode != "obo":
        raise ValueError("The Activity endpoint requires AGENT_AUTH_MODE=obo")
    version = version or os.environ.get("FOUNDRY_AGENT_VERSION")
    oauth_connection_name = oauth_connection_name or os.environ.get("AGENT_OAUTH_CONNECTION_NAME")
    if not version or not oauth_connection_name:
        raise ValueError("FOUNDRY_AGENT_VERSION and AGENT_OAUTH_CONNECTION_NAME are required")
    if channel_auth is None:
        config = load_configuration_from_env(os.environ)
        connection = config.get("CONNECTIONS", {}).get("SERVICE_CONNECTION", {}).get("SETTINGS", {})
        channel_auth = AgentAuthConfiguration(**connection)
    if not channel_auth.CLIENT_ID or not channel_auth.TENANT_ID:
        raise ValueError("Channel service connection client ID and tenant ID are required")
    if channel_auth.TENANT_ID != settings.identity.tenant_id:
        raise ValueError("Channel service connection must use the configured agent tenant")
    if channel_auth.ANONYMOUS_ALLOWED:
        raise ValueError("Anonymous channel authentication is not supported")
    channel_auth.VALIDATE_ISSUER = True
    connections = MsalConnectionManager(connections_configurations={"SERVICE_CONNECTION": channel_auth})
    adapter = CloudAdapter(connection_manager=connections)
    storage = MemoryStorage()
    authorization = Authorization(
        storage,
        connections,
        auth_handlers={
            AUTH_HANDLER: AuthHandler(
                name=AUTH_HANDLER,
                auth_type="UserAuthorization",
                abs_oauth_connection_name=oauth_connection_name,
            )
        },
    )
    agent = EnterpriseArchitectApplication(
        ApplicationOptions(
            storage=storage,
            bot_app_id=channel_auth.CLIENT_ID,
            long_running_messages=True,
        ),
        authorization,
        settings,
        version,
    )

    @jwt_authorization_decorator
    async def messages(request: web.Request) -> web.Response:
        response = await start_agent_process(request, agent, adapter)
        if response is None:
            raise web.HTTPInternalServerError(text="The channel adapter returned no response")
        return response

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app = web.Application()
    app["agent_configuration"] = channel_auth
    app[AGENT_APP_KEY] = agent
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/health", health)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), host="127.0.0.1", port=int(os.environ.get("PORT", "3978")))


if __name__ == "__main__":
    main()
