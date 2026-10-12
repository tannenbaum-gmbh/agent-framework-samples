# Prompt agent

This minimal sample creates a Microsoft Agent Framework `Agent`, sends it one prompt, and
prints the response.

## Prerequisites

- Deploy the Microsoft Foundry environment in [`infra/`](../../infra).
- Sign in with `az login`.
- Use Python 3.10 or newer.

## Run

```bash
cd samples/prompt-agent
pip install -r requirements.txt

cp .env.example .env
# Fill in the project endpoint, then export the settings.
export $(grep -v '^#' .env | xargs)

python -m app.main "What are three benefits of using AI agents?"
```

If no prompt is supplied, the sample uses a built-in default prompt.
