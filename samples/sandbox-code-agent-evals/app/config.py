# Copyright (c) Microsoft. All rights reserved.
"""Configuration for the sandbox code agent sample, sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class FoundrySettings:
    """Runtime settings for connecting to a Microsoft Foundry project."""

    project_endpoint: str
    model_deployment: str


@dataclass(frozen=True)
class SandboxSettings:
    """Runtime settings for the Azure Container Apps sandbox group (preview)."""

    subscription_id: str
    resource_group: str
    sandbox_group: str
    region: str
    disk_image: str


@dataclass(frozen=True)
class Settings:
    """All settings required by the sample."""

    foundry: FoundrySettings
    sandbox: SandboxSettings


def _require(name: str, hint: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is required. {hint}")
    return value


def get_settings() -> Settings:
    """Build `Settings` from environment variables.

    Environment variables:
        FOUNDRY_PROJECT_ENDPOINT: The AI Foundry project endpoint, e.g.
            ``https://<account>.services.ai.azure.com/api/projects/<project>``.
            This is the ``foundryProjectEndpoint`` output of ``infra/main.bicep``.
        FOUNDRY_MODEL_DEPLOYMENT: The name of the model deployment to use.
            Defaults to ``gpt-5``, matching ``infra/main.bicep``'s default.
        AZURE_SUBSCRIPTION_ID: Subscription that holds the sandbox group.
        AZURE_RESOURCE_GROUP: Resource group that holds the sandbox group.
        AZURE_SANDBOX_GROUP: Name of the ``Microsoft.App/sandboxGroups`` resource.
        AZURE_SANDBOX_REGION: Region of the sandbox group, e.g. ``westus2``.
        SANDBOX_DISK_IMAGE: Public disk image for new sandboxes. Defaults to
            ``python-3.14``. Call ``SandboxGroupClient.list_public_disk_images()``
            to discover the images currently available.
    """
    infra_hint = "Set it to the matching output of the infra/ deployment (see infra/README.md)."
    foundry = FoundrySettings(
        project_endpoint=_require("FOUNDRY_PROJECT_ENDPOINT", infra_hint),
        model_deployment=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-5"),
    )
    sandbox = SandboxSettings(
        subscription_id=_require("AZURE_SUBSCRIPTION_ID", "Run `az account show --query id -o tsv` to find it."),
        resource_group=_require("AZURE_RESOURCE_GROUP", infra_hint),
        sandbox_group=_require("AZURE_SANDBOX_GROUP", infra_hint),
        region=_require("AZURE_SANDBOX_REGION", infra_hint),
        disk_image=os.environ.get("SANDBOX_DISK_IMAGE", "python-3.14"),
    )
    return Settings(foundry=foundry, sandbox=sandbox)
