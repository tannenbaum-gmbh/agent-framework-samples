"""Environment configuration for the architecture review workflow."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    project_endpoint: str
    architect_agent_name: str = "enterprise-architect-prompt-agent"
    mslearn_agent_name: str = "mslearn-prompt-agent"

    @property
    def mslearn_a2a_endpoint(self) -> str:
        return (
            f"{self.project_endpoint.rstrip('/')}/agents/"
            f"{self.mslearn_agent_name}/endpoint/protocols/a2a"
        )


def get_settings() -> Settings:
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT environment variable is required.")
    return Settings(
        project_endpoint=endpoint.rstrip("/"),
        architect_agent_name=(
            os.environ.get("ARCHITECT_AGENT_NAME", "").strip()
            or "enterprise-architect-prompt-agent"
        ),
        mslearn_agent_name=(
            os.environ.get("MSLEARN_AGENT_NAME", "").strip() or "mslearn-prompt-agent"
        ),
    )
