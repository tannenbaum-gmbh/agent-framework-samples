"""Configuration for the enterprise architect prompt-agent sample."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    project_endpoint: str
    model_deployment: str
    a2a_connection_name: str


def get_settings() -> Settings:
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT environment variable is required.")

    connection_name = os.environ.get("MSLEARN_A2A_CONNECTION_NAME", "").strip()
    if not connection_name:
        raise RuntimeError(
            "MSLEARN_A2A_CONNECTION_NAME environment variable is required."
        )

    model = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "").strip() or "gpt-5-mini"
    return Settings(
        project_endpoint=endpoint,
        model_deployment=model,
        a2a_connection_name=connection_name,
    )
