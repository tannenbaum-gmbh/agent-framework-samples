"""Configuration for existing Search and Foundry resources."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

SAMPLE_DIR = Path(__file__).resolve().parents[1]
SEARCH_API_VERSION = "2026-08-01-preview"
CONNECTION_API_VERSION = "2025-10-01-preview"
SEMANTIC_CONFIGURATION = "foundry-iq-semantic"


@dataclass(frozen=True)
class Settings:
    search_endpoint: str
    index_name: str
    project_endpoint: str
    openai_endpoint: str
    model_deployment: str
    model_name: str = "gpt-5"
    prefix: str = "foundry-iq"
    project_resource_id: str = ""

    @property
    def knowledge_base_name(self) -> str:
        return f"{self.prefix}-kb"

    @property
    def source_names(self) -> tuple[str, str, str]:
        return (
            f"{self.prefix}-guidelines",
            f"{self.prefix}-mslearn",
            f"{self.prefix}-web",
        )

    @property
    def connection_name(self) -> str:
        return f"{self.prefix}-connection"

    @property
    def agent_name(self) -> str:
        return f"{self.prefix}-agent"

    @property
    def mcp_endpoint(self) -> str:
        return (
            f"{self.search_endpoint}/knowledgebases/{self.knowledge_base_name}"
            f"/mcp?api-version={SEARCH_API_VERSION}"
        )

    def require_project_resource_id(self) -> str:
        if not self.project_resource_id:
            raise RuntimeError(
                "FOUNDRY_PROJECT_RESOURCE_ID is required to manage the MCP connection."
            )
        return self.project_resource_id


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is required.")
    return value


def endpoint(name: str, *, project: bool = False) -> str:
    value = required(name).rstrip("/")
    parsed = urlparse(value)
    valid_path = (
        bool(re.fullmatch(r"/api/projects/[a-zA-Z0-9_-]+", parsed.path))
        if project else not parsed.path
    )
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not valid_path
    ):
        kind = "project URL ending in /api/projects/<name>" if project else "resource endpoint without a path"
        raise RuntimeError(f"{name} must be an HTTPS {kind}.")
    return value


def search_name(value: str, variable: str, *, max_length: int) -> str:
    if (
        not re.fullmatch(rf"[a-z0-9][a-z0-9-]{{0,{max_length - 2}}}[a-z0-9]", value)
        or "--" in value
    ):
        raise RuntimeError(
            f"{variable} must be 2-{max_length} lowercase letters, digits, or single "
            "hyphens, starting and ending with a letter or digit."
        )
    return value


def get_settings() -> Settings:
    load_dotenv(SAMPLE_DIR / ".env", override=False)
    prefix = search_name(
        os.environ.get("FOUNDRY_IQ_RESOURCE_PREFIX", "foundry-iq").strip(),
        "FOUNDRY_IQ_RESOURCE_PREFIX",
        max_length=40,
    )
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "").strip() or (
        os.environ.get("AZURE_SEARCH_RESOURCE_PREFIX", "classic-rag").strip() + "-chunks"
    )
    search_name(index_name, "AZURE_SEARCH_INDEX_NAME", max_length=128)
    resource_id = os.environ.get("FOUNDRY_PROJECT_RESOURCE_ID", "").strip().rstrip("/")
    if resource_id and not re.fullmatch(
        r"/subscriptions/[\w-]+/resourceGroups/[\w.()-]+/providers/"
        r"Microsoft\.CognitiveServices/accounts/[\w-]+/projects/[\w-]+",
        resource_id,
        flags=re.IGNORECASE,
    ):
        raise RuntimeError("FOUNDRY_PROJECT_RESOURCE_ID must identify a Foundry project.")
    return Settings(
        search_endpoint=endpoint("AZURE_SEARCH_ENDPOINT"),
        index_name=index_name,
        project_endpoint=endpoint("FOUNDRY_PROJECT_ENDPOINT", project=True),
        openai_endpoint=endpoint("AZURE_OPENAI_ENDPOINT"),
        model_deployment=required("FOUNDRY_MODEL_DEPLOYMENT"),
        model_name=os.environ.get("FOUNDRY_MODEL_NAME", "").strip() or "gpt-5",
        prefix=prefix,
        project_resource_id=resource_id,
    )
