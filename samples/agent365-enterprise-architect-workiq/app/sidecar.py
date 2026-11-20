# Copyright (c) Microsoft. All rights reserved.
"""Token credential backed by the Microsoft Entra SDK for AgentID (auth sidecar).

The sidecar holds the blueprint credential and performs the blueprint -> agent identity
FMI exchange, OBO, and token caching. This process only talks to it over HTTP, so it
never sees a blueprint secret or managed-identity assertion.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx
import jwt
from azure.core.credentials import AccessToken
from azure.core.credentials_async import AsyncTokenCredential

DEFAULT_SIDECAR_URL = "http://localhost:5000"
# DownstreamApis entries configured on the sidecar; scopes are overridden per call.
# The app-only entry must set RequestAppToken=true, otherwise the sidecar attempts a user flow.
DEFAULT_SERVICE_NAME = "Agent"
APP_SERVICE_NAME = "AgentApp"
OBSERVABILITY_SCOPE = "api://9b975845-388f-4429-889e-eab1ef63949c/.default"
REFRESH_SKEW_SECONDS = 300


class SidecarError(RuntimeError):
    """The sidecar could not issue a token."""


class SidecarCredential(AsyncTokenCredential):
    """Acquire agent-identity tokens from the sidecar.

    With a ``user_assertion`` (a blueprint-audience user token) it requests delegated OBO
    tokens. The Agent 365 observability audience always uses the app-only agent token.
    """

    def __init__(
        self,
        agent_identity_client_id: str,
        *,
        user_assertion: str | None = None,
        sidecar_url: str = DEFAULT_SIDECAR_URL,
        service_name: str = DEFAULT_SERVICE_NAME,
        app_service_name: str = APP_SERVICE_NAME,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not agent_identity_client_id:
            raise ValueError("The agent identity client ID is required")
        self._agent_id = agent_identity_client_id
        self._user_assertion = (user_assertion or "").strip() or None
        self._service = quote(service_name, safe="")
        self._app_service = quote(app_service_name, safe="")
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(base_url=sidecar_url, timeout=60)
        self._tokens: dict[tuple[bool, tuple[str, ...]], AccessToken] = {}

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        if not scopes:
            raise ValueError("At least one scope is required")
        autonomous = OBSERVABILITY_SCOPE in scopes
        if autonomous and len(scopes) != 1:
            raise ValueError("The observability scope cannot be combined with other scopes")
        if not autonomous and self._user_assertion is None:
            raise ValueError("A user assertion is required for delegated OBO tokens")

        key = (autonomous, scopes)
        cached = self._tokens.get(key)
        if cached is not None and cached.expires_on > time.time() + REFRESH_SKEW_SECONDS:
            return cached

        params: list[tuple[str, str]] = [("AgentIdentity", self._agent_id)]
        params += [("optionsOverride.Scopes", scope) for scope in scopes]
        if autonomous:
            path, headers = f"/AuthorizationHeaderUnauthenticated/{self._app_service}", {}
        else:
            path, headers = f"/AuthorizationHeader/{self._service}", {"Authorization": f"Bearer {self._user_assertion}"}

        try:
            response = await self._http.get(path, params=params, headers=headers)
        except httpx.HTTPError as error:
            raise SidecarError(f"Sidecar request failed: {error}") from error
        if response.status_code != 200:
            raise SidecarError(f"Sidecar token request failed ({response.status_code}): {response.text[:500]}")

        header = response.json().get("authorizationHeader", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise SidecarError("Sidecar returned an unexpected authorization header")
        expires_on = int(jwt.decode(token, options={"verify_signature": False})["exp"])
        access_token = AccessToken(token, expires_on)
        self._tokens[key] = access_token
        return access_token

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()
