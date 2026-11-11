# Copyright (c) Microsoft. All rights reserved.
"""Run the agent as a local process; all token negotiation goes through the Entra SDK sidecar."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .config import LocalSettings, get_local_settings
from .sandbox_agent import run_request
from .sidecar import SidecarCredential

DEFAULT_QUESTION = "Find architecture guidance in my Microsoft 365 documents and summarize it with source links."


def build_request(settings: LocalSettings, question: str, user_assertion: str) -> dict[str, object]:
    request: dict[str, object] = {
        "tenant_id": settings.tenant_id,
        "blueprint_app_id": settings.blueprint_app_id,
        "agent_identity_client_id": settings.agent_identity_client_id,
        "project_endpoint": settings.foundry_project_endpoint,
        "model_deployment": settings.foundry_model_deployment,
        "question": question,
        "user_assertion": user_assertion,
        "workiq_enable_actions": settings.workiq_enable_actions,
        "observability_enabled": settings.observability_enabled,
        "observability_capture_content": settings.observability_capture_content,
    }
    if settings.observability_enabled:
        request.update(
            observability_sponsor_id=settings.observability_sponsor_id,
            observability_sponsor_name=settings.observability_sponsor_name,
            observability_sponsor_email=settings.observability_sponsor_email,
        )
    return request


async def main() -> int:
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION
    settings = get_local_settings()
    user_assertion = Path(settings.user_assertion_file).read_text(encoding="utf-8").strip()
    # One credential serves delegated Foundry/Work IQ tokens and, by audience, app-only telemetry.
    credential = SidecarCredential(
        settings.agent_identity_client_id, user_assertion=user_assertion, sidecar_url=settings.sidecar_url
    )
    try:
        result = await run_request(build_request(settings, question, user_assertion), credential, credential)
    finally:
        await credential.close()
    print(f"Agent identity: {result['agent_identity']}")
    print(f"Blueprint:      {result['blueprint']}")
    print(f"User-Agent:     {result['user_agent']}")
    print()
    print(result["answer"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
