"""Configuration for the launcher and the sandbox runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is required.")
    return value


@dataclass(frozen=True)
class RuntimeSettings:
    project_endpoint: str
    model_deployment: str
    api_key: str

    def __post_init__(self) -> None:
        endpoint = urlparse(self.project_endpoint)
        if endpoint.scheme != "https" or not endpoint.hostname:
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT must be an HTTPS URL.")
        if len(self.api_key) < 32 or not self.api_key.isascii():
            raise ValueError("SERVICE_API_KEY must contain at least 32 ASCII characters.")


@dataclass(frozen=True)
class Settings:
    runtime: RuntimeSettings
    subscription_id: str
    resource_group: str
    sandbox_group: str
    sandbox_region: str
    sandbox_disk_image: str = "python-3.14"


def get_settings() -> Settings:
    return Settings(
        runtime=RuntimeSettings(
            project_endpoint=require("FOUNDRY_PROJECT_ENDPOINT"),
            model_deployment=(
                os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "").strip()
                or os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "").strip()
                or "gpt-5.3-codex-2"
            ),
            api_key=require("SERVICE_API_KEY"),
        ),
        subscription_id=require("AZURE_SUBSCRIPTION_ID"),
        resource_group=require("AZURE_RESOURCE_GROUP"),
        sandbox_group=require("AZURE_SANDBOX_GROUP"),
        sandbox_region=require("AZURE_SANDBOX_REGION"),
        sandbox_disk_image=os.environ.get("SANDBOX_DISK_IMAGE", "").strip() or "python-3.14",
    )
