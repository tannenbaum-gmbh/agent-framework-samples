"""Read atomically refreshed, short-lived tokens without copying refresh credentials."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from azure.core.credentials import AccessToken

FOUNDRY_SCOPE = "https://ai.azure.com/.default"
WORKIQ_SCOPE = "api://workiq.svc.cloud.microsoft/.default"


def read_token(path: Path, audience: str) -> AccessToken:
    data = json.loads(path.read_text(encoding="utf-8"))[audience]
    token = AccessToken(data["token"], int(data["expires_on"]))
    if not token.token or token.expires_on <= time.time() + 60:
        raise RuntimeError(f"{audience} token missing or expiring; check the launcher.")
    return token


class FileTokenCredential:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        if scopes != (FOUNDRY_SCOPE,):
            raise ValueError("This credential serves only the Foundry audience.")
        return read_token(self.path, "foundry")
