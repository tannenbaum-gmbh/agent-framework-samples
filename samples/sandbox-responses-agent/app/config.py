# Copyright (c) Microsoft. All rights reserved.
"""Launcher configuration loaded from environment variables."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field

DEFAULT_PORT = 8088
MAX_ALLOWED_CIDRS = 10


@dataclass(frozen=True)
class Settings:
    foundry_project_endpoint: str
    foundry_model_deployment: str
    subscription_id: str
    resource_group: str
    sandbox_group: str
    sandbox_region: str
    sandbox_disk_image: str = "python-3.14"
    allowed_cidrs: tuple[str, ...] = field(default_factory=tuple)
    idle_suspend_seconds: int = 3600
    port: int = DEFAULT_PORT


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def parse_cidrs(raw: str) -> tuple[str, ...]:
    """Parse and validate a comma-separated list of network-aligned CIDRs."""
    cidrs = tuple(item.strip() for item in raw.split(",") if item.strip())
    if len(cidrs) > MAX_ALLOWED_CIDRS:
        raise RuntimeError(f"SANDBOX_ALLOWED_CIDRS supports at most {MAX_ALLOWED_CIDRS} entries")
    for cidr in cidrs:
        try:
            ipaddress.ip_network(cidr, strict=True)
        except ValueError as error:
            raise RuntimeError(f"Invalid CIDR in SANDBOX_ALLOWED_CIDRS: {cidr!r} ({error})") from error
    return cidrs


def get_settings() -> Settings:
    """Build settings from the environment."""
    return Settings(
        foundry_project_endpoint=_require("FOUNDRY_PROJECT_ENDPOINT"),
        foundry_model_deployment=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT", "").strip() or "gpt-5",
        subscription_id=_require("AZURE_SUBSCRIPTION_ID"),
        resource_group=_require("AZURE_RESOURCE_GROUP"),
        sandbox_group=_require("AZURE_SANDBOX_GROUP"),
        sandbox_region=_require("AZURE_SANDBOX_REGION"),
        sandbox_disk_image=os.environ.get("SANDBOX_DISK_IMAGE", "").strip() or "python-3.14",
        allowed_cidrs=parse_cidrs(os.environ.get("SANDBOX_ALLOWED_CIDRS", "")),
        idle_suspend_seconds=int(os.environ.get("SANDBOX_IDLE_SUSPEND_SECONDS", "").strip() or 3600),
    )
