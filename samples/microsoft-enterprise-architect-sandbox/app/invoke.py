"""Call the sandbox Responses API, not a Foundry hosted-agent endpoint."""

from __future__ import annotations

import argparse

from openai import OpenAI

from .config import require


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
    with OpenAI(
        base_url=url.rstrip("/") + "/",
        api_key=require("SERVICE_API_KEY"),
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
