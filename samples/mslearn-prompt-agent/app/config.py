"""Configuration for the Microsoft Learn prompt-agent sample."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    project_endpoint: str
    model_deployment: str


def get_settings() -> Settings:
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT environment variable is required.")
    model = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "").strip() or "gpt-5"
    return Settings(project_endpoint=endpoint, model_deployment=model)
