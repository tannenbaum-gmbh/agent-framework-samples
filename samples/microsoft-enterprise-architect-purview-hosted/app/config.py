"""Configuration shared by deployment, invocation, and the hosted runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Settings:
    project_endpoint: str
    model_deployment: str
    workiq_connection_name: str = ""
    workiq_connection_id: str = ""
    purview_tenant_id: str = ""
    purview_app_id: str = ""
    purview_user_id: str = ""


def required_uuid(name: str) -> str:
    value = os.environ.get(name, "").strip()
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a Microsoft Entra object/client ID (UUID).") from exc


def get_settings() -> Settings:
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT environment variable is required.")
    return Settings(
        project_endpoint=endpoint,
        model_deployment=(
            os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "").strip()
            or os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "").strip()
            or "gpt-5.3-codex-2"
        ),
        workiq_connection_name=os.environ.get("WORKIQ_CONNECTION_NAME", "").strip(),
        workiq_connection_id=os.environ.get("WORKIQ_CONNECTION_ID", "").strip(),
        purview_tenant_id=required_uuid("PURVIEW_TENANT_ID"),
        purview_app_id=required_uuid("PURVIEW_APP_ID"),
        purview_user_id=required_uuid("PURVIEW_USER_ID"),
    )
