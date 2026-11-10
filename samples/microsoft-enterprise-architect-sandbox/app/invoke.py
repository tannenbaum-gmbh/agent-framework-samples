"""Call the sandbox Responses API using the caller's Azure CLI Work IQ token."""

from __future__ import annotations

import argparse

from azure.identity import AzureCliCredential, get_bearer_token_provider
from openai import OpenAI

from .config import require
from .tokens import WORKIQ_SCOPE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", default=(
        "Design supplier invoice automation on Azure. Ask for my internal guideline "
        "location, state assumptions, and cite current Microsoft documentation."
    ))
    args = parser.parse_args()
    url = require("SERVICE_URL")
    if not url.startswith("https://"):
        raise ValueError("SERVICE_URL must use HTTPS.")
    with AzureCliCredential() as credential, OpenAI(
        base_url=url.rstrip("/") + "/",
        api_key=get_bearer_token_provider(credential, WORKIQ_SCOPE),
        default_headers={"X-API-Key": require("SERVICE_API_KEY")},
        timeout=600,
        max_retries=0,
    ) as client:
        response = client.responses.create(
            model="mea-sandbox-agent", input=args.question, store=False,
        )
    if response.status != "completed":
        raise RuntimeError(f"Response {response.id}: {response.status}; {response.error}")
    if not response.output_text:
        raise RuntimeError(f"Response {response.id} completed without text; inspect tool/consent errors.")
    print(response.output_text)


if __name__ == "__main__":
    main()
