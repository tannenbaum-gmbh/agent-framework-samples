"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    project_endpoint: str
    model_deployment: str


def get_settings() -> Settings:
    project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not project_endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT environment variable is required.")
    return Settings(
        project_endpoint=project_endpoint,
        model_deployment=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-5"),
    )