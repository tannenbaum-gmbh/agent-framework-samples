"""Configuration shared by deployment, invocation, and the hosted runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    project_endpoint: str
    model_deployment: str
    workiq_connection_name: str = ""
    workiq_toolbox_name: str = ""
    workiq_toolbox_version: str = ""


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
        workiq_toolbox_name=os.environ.get("WORKIQ_TOOLBOX_NAME", "").strip(),
        workiq_toolbox_version=os.environ.get("WORKIQ_TOOLBOX_VERSION", "").strip(),
    )
