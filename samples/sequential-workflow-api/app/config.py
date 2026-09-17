# Copyright (c) Microsoft. All rights reserved.
"""Configuration for the sequential workflow sample, sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Runtime settings for connecting to a Microsoft Foundry project."""

    project_endpoint: str
    model_deployment: str


def get_settings() -> Settings:
    """Build `Settings` from environment variables.

    Environment variables:
        FOUNDRY_PROJECT_ENDPOINT: The AI Foundry project endpoint, e.g.
            ``https://<account>.services.ai.azure.com/api/projects/<project>``.
            This is the ``foundryProjectEndpoint`` output of ``infra/main.bicep``.
        FOUNDRY_MODEL_DEPLOYMENT: The name of the model deployment to use.
            Defaults to ``gpt-5``, matching ``infra/main.bicep``'s default.
    """
    project_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not project_endpoint:
        raise RuntimeError(
            "FOUNDRY_PROJECT_ENDPOINT environment variable is required. Set it to the AI Foundry "
            "project endpoint produced by the infra/ deployment (see infra/README.md)."
        )
    model_deployment = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "gpt-5")
    return Settings(project_endpoint=project_endpoint, model_deployment=model_deployment)
