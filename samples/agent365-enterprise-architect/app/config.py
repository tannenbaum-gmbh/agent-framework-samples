# Copyright (c) Microsoft. All rights reserved.
"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

# Microsoft Graph PowerShell: a Microsoft first-party public client that supports
# dynamic consent for the Agent ID Graph scopes used by the management CLI.
DEFAULT_GRAPH_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"


@dataclass(frozen=True)
class IdentitySettings:
    tenant_id: str
    graph_client_id: str = DEFAULT_GRAPH_CLIENT_ID
    blueprint_app_id: str | None = None
    agent_identity_id: str | None = None
    agent_identity_client_id: str | None = None
    blueprint_client_secret: str | None = None
    managed_identity_client_id: str | None = None
    sponsor_id: str | None = None


@dataclass(frozen=True)
class Settings:
    foundry_project_endpoint: str
    foundry_model_deployment: str
    subscription_id: str
    resource_group: str
    sandbox_group: str
    sandbox_region: str
    sandbox_disk_image: str
    identity: IdentitySettings
    observability_enabled: bool = False
    observability_capture_content: bool = False
    observability_sponsor_id: str | None = None
    observability_sponsor_name: str | None = None
    observability_sponsor_email: str | None = None
    workiq_connection_name: str | None = None


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def _optional(name: str) -> str | None:
    return os.environ.get(name) or None


def _boolean(name: str) -> bool:
    value = os.environ.get(name, "false").strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError(f"{name} must be true or false")
    return value == "true"


def get_identity_settings() -> IdentitySettings:
    """Build Entra Agent ID settings from the environment."""
    return IdentitySettings(
        tenant_id=_require("AZURE_TENANT_ID"),
        graph_client_id=os.environ.get("GRAPH_CLIENT_ID") or DEFAULT_GRAPH_CLIENT_ID,
        blueprint_app_id=_optional("AGENT_BLUEPRINT_APP_ID"),
        agent_identity_id=_optional("AGENT_IDENTITY_ID"),
        agent_identity_client_id=_optional("AGENT_IDENTITY_CLIENT_ID"),
        blueprint_client_secret=_optional("AGENT_BLUEPRINT_CLIENT_SECRET"),
        managed_identity_client_id=_optional("AGENT_BLUEPRINT_MANAGED_IDENTITY_CLIENT_ID"),
        sponsor_id=_optional("AGENT_SPONSOR_ID"),
    )


def get_settings() -> Settings:
    """Build launcher settings from the environment."""
    identity = get_identity_settings()
    if not identity.blueprint_app_id or not identity.agent_identity_client_id:
        raise RuntimeError("AGENT_BLUEPRINT_APP_ID and AGENT_IDENTITY_CLIENT_ID environment variables are required")
    observability_enabled = _boolean("ENABLE_A365_OBSERVABILITY_EXPORTER")
    return Settings(
        foundry_project_endpoint=_require("FOUNDRY_PROJECT_ENDPOINT"),
        foundry_model_deployment=(
            os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "").strip() or "gpt-5.3-codex-2"
        ),
        subscription_id=_require("AZURE_SUBSCRIPTION_ID"),
        resource_group=_require("AZURE_RESOURCE_GROUP"),
        sandbox_group=_require("AZURE_SANDBOX_GROUP"),
        sandbox_region=_require("AZURE_SANDBOX_REGION"),
        sandbox_disk_image=os.environ.get("SANDBOX_DISK_IMAGE", "python-3.14"),
        identity=identity,
        observability_enabled=observability_enabled,
        observability_capture_content=_boolean("A365_CAPTURE_CONTENT"),
        observability_sponsor_id=_require("A365_SPONSOR_ID") if observability_enabled else None,
        observability_sponsor_name=_require("A365_SPONSOR_NAME") if observability_enabled else None,
        observability_sponsor_email=_require("A365_SPONSOR_EMAIL") if observability_enabled else None,
        workiq_connection_name=os.environ.get("WORKIQ_CONNECTION_NAME", "").strip() or None,
    )
