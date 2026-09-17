# Copyright (c) Microsoft. All rights reserved.
"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    foundry_project_endpoint: str
    foundry_model_deployment: str
    subscription_id: str
    resource_group: str
    sandbox_group: str
    sandbox_region: str
    sandbox_disk_image: str


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def get_settings() -> Settings:
    """Build settings from the environment."""
    return Settings(
        foundry_project_endpoint=_require("FOUNDRY_PROJECT_ENDPOINT"),
        foundry_model_deployment=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-5"),
        subscription_id=_require("AZURE_SUBSCRIPTION_ID"),
        resource_group=_require("AZURE_RESOURCE_GROUP"),
        sandbox_group=_require("AZURE_SANDBOX_GROUP"),
        sandbox_region=_require("AZURE_SANDBOX_REGION"),
        sandbox_disk_image=os.environ.get("SANDBOX_DISK_IMAGE", "python-3.14"),
    )