"""Environment configuration for existing Azure resources."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

SAMPLE_DIR = Path(__file__).resolve().parents[1]
GUIDELINES_DIR = (
    SAMPLE_DIR.parent / "enterprise-architect-prompt-agent" / "internal-guidelines"
)
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


@dataclass(frozen=True)
class Settings:
    search_endpoint: str
    storage_account_url: str
    storage_resource_id: str
    openai_endpoint: str
    embedding_deployment: str
    chat_deployment: str = ""
    prefix: str = "classic-rag"

    @property
    def index_name(self) -> str:
        return f"{self.prefix}-chunks"

    @property
    def container_name(self) -> str:
        return f"{self.prefix}-documents"

    @property
    def data_source_name(self) -> str:
        return f"{self.prefix}-blobs"

    @property
    def skillset_name(self) -> str:
        return f"{self.prefix}-skills"

    @property
    def indexer_name(self) -> str:
        return f"{self.prefix}-indexer"


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is required.")
    return value


def endpoint(name: str) -> str:
    value = required(name).rstrip("/")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise RuntimeError(f"{name} must be an HTTPS resource endpoint without a path.")
    return value


def get_settings() -> Settings:
    load_dotenv(SAMPLE_DIR / ".env", override=False)
    prefix = os.environ.get("AZURE_SEARCH_RESOURCE_PREFIX", "classic-rag").strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,38}[a-z0-9]", prefix) or "--" in prefix:
        raise RuntimeError(
            "AZURE_SEARCH_RESOURCE_PREFIX must be 3-40 lowercase letters, digits, "
            "or single hyphens, starting with a letter and ending with a letter or digit."
        )
    resource_id = required("AZURE_STORAGE_RESOURCE_ID")
    if not re.fullmatch(
        r"/subscriptions/[^/;]+/resourceGroups/[^/;]+/providers/"
        r"Microsoft\.Storage/storageAccounts/[a-z0-9]{3,24}",
        resource_id,
        flags=re.IGNORECASE,
    ):
        raise RuntimeError("AZURE_STORAGE_RESOURCE_ID must identify a storage account.")
    return Settings(
        search_endpoint=endpoint("AZURE_SEARCH_ENDPOINT"),
        storage_account_url=endpoint("AZURE_STORAGE_ACCOUNT_URL"),
        storage_resource_id=resource_id,
        openai_endpoint=endpoint("AZURE_OPENAI_ENDPOINT"),
        embedding_deployment=required("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        chat_deployment=os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "").strip(),
        prefix=prefix,
    )
