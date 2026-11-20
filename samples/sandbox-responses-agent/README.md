# Sandbox-hosted Responses API agent

A minimal Agent Framework agent that runs as a **long-lived HTTP service inside an
[Azure Container Apps sandbox](https://learn.microsoft.com/azure/container-apps/sandboxes-overview)**
and exposes an OpenAI-compatible **Responses API** endpoint. Model inference uses the
Microsoft Foundry project and model deployment from [`infra/`](../../infra).

| Sample | What runs in the sandbox | How you call it |
| --- | --- | --- |
| [`sandbox-hosted-agent`](../sandbox-hosted-agent) | One agent run per launch, then the sandbox is deleted | Local CLI only |
| **`sandbox-responses-agent`** (this sample) | A persistent Responses API server | `POST https://<sandbox-port-url>/responses` from any HTTP client |
| [`mslearn-hosted-agent`](../mslearn-hosted-agent) | Nothing (Foundry hosted agent) | Authenticated Foundry endpoint |

## Architecture

```text
 your machine (launcher)                    ACA sandbox (microVM)
 ┌──────────────────────┐   data plane      ┌────────────────────────────────────────┐
 │ python -m app.cli up │ ───────────────▶  │ sandbox_server.py                      │
 │  - creates sandbox   │  upload, exec,    │  ResponsesHostServer  :8088            │
 │  - uploads server    │  ports            │   └─ API key middleware                │
 │  - gets Entra token  │                   │   └─ Agent ── FoundryChatClient ───────┼──▶ Foundry model
 └──────────────────────┘                   └──────────────▲─────────────────────────┘    deployment
                                                           │ HTTPS port URL (ingress)
 any client ── POST /responses  (Authorization: Bearer <key>) ┘
```

`python -m app.cli up`:

1. Creates a sandbox from the configured Python disk image (auto-suspends when idle).
2. Uploads `app/sandbox_server.py`, `requirements-sandbox.txt`, a non-secret config file,
   a short-lived Foundry token (`https://ai.azure.com/.default`), and a random API key.
   The secrets are written as `0600` files and never placed on a command line.
3. Installs Agent Framework inside the sandbox and starts the server in the background.
4. Waits until `/readiness` answers **from inside the sandbox**, then publishes port 8088
   through sandbox ingress and stores the URL and API key in `.sandbox-state.json` (`0600`,
   git-ignored).

If any step fails, the sandbox is deleted. The server uses
[`ResponsesHostServer`](https://learn.microsoft.com/agent-framework/hosting/foundry-hosted-agent),
the same Responses host used by Foundry hosted agents. It creates a new agent and model
client for every request.

## Security model

- **Ingress:** the sandbox port is published with anonymous ingress auth because
  ACA sandbox Entra ID port auth is an interactive, email-based sign-in, which doesn't
  suit API clients. Instead, the server requires a 256-bit API key on every route except
  `/readiness` (`Authorization: Bearer <key>` or `api-key: <key>`, compared in constant time).
  Set `SANDBOX_ALLOWED_CIDRS` to add a source-IP allow-list at the sandbox ingress.
- **Model access:** the sandbox gets only a short-lived Foundry access token (about 1 hour),
  not your Azure credentials. When it expires, model calls fail until you run
  `python -m app.cli refresh`, which atomically replaces the token file. The server reads it
  again on each call, so no restart is needed.
- **Trust boundary:** the server and agent are trusted code, and the agent has no tools.
  Don't add tools that run model-generated or user-supplied code in this sandbox, because the
  token and API key are readable there. Use a separate sandbox for that, as shown in
  [`sandbox-code-agent-evals`](../sandbox-code-agent-evals).
- **Egress:** the sandbox needs HTTPS access to PyPI (on first start) and to the Foundry
  project endpoint. In production, restrict egress to those hosts with the sandbox group's
  egress policy, and bake the dependencies into a custom disk image.

This is a sample, not a production gateway. For production, put the endpoint behind your
own identity-aware gateway or private endpoint, and use managed identity instead of a
transferred token once your sandbox deployment supports it.

## Prerequisites

- Deploy the environment in [`infra/README.md`](../../infra/README.md) with
  `deploySandboxGroup=true`.
- Sign in with `az login`. Your identity needs **Container Apps SandboxGroup Data Owner**
  on the sandbox group and permission to invoke the Foundry model deployment
  (Foundry User / Azure AI User on the project).
- Python 3.10 or newer locally.

## Run

```bash
cd samples/sandbox-responses-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in the infra outputs (foundryProjectEndpoint, gpt5DeploymentName,
# sandboxGroupName, sandboxGroupLocation), then load the file:
set -a; source .env; set +a

python -m app.cli up
```

The first `up` installs the packages inside the sandbox, so it takes a few minutes.
It prints the public `…/responses` endpoint URL assigned by sandbox ingress.

### Call the endpoint

With the bundled client:

```bash
python -m app.cli invoke "Explain why process isolation matters in two sentences."
```

With `curl`:

```bash
URL=$(python -c "import json;print(json.load(open('.sandbox-state.json'))['url'])")
KEY=$(python -c "import json;print(json.load(open('.sandbox-state.json'))['api_key'])")

curl -sS "$URL/responses" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"input":"What is an Azure Container Apps sandbox?","stream":false,"store":false}'
```

With the OpenAI Python SDK (`pip install openai`), because the endpoint uses the
Responses protocol:

```python
from openai import OpenAI

client = OpenAI(base_url=URL, api_key=KEY)
print(client.responses.create(model="sandbox-responses-agent", input="Hello!", store=False).output_text)
```

The agent's Foundry deployment always serves the request. The `model` field is required
by the SDK but doesn't select a model. Streaming (`"stream": true`) is supported too.

### Keep it running

```bash
python -m app.cli refresh   # resume the sandbox if suspended and renew the Foundry token
```

The sandbox auto-suspends after `SANDBOX_IDLE_SUSPEND_SECONDS` (default 3600) without
traffic, preserving memory so the server process continues after resume. `refresh` resumes
it and renews the token. Run `refresh` before the expiry time printed by `up`.

### Clean up

```bash
python -m app.cli down
```

This deletes the sandbox and the local state file.

## Files

| File | Purpose |
| --- | --- |
| `app/sandbox_server.py` | Self-contained Responses API server that runs **inside** the sandbox (agent, Foundry client, API key middleware, refreshable token credential). |
| `app/host.py` | Host-side sandbox provisioning, upload, server start, readiness check, port exposure, token refresh, and cleanup. |
| `app/cli.py` | `up`, `invoke`, `refresh`, and `down` commands. |
| `app/config.py` | Launcher configuration from environment variables. |
| `requirements.txt` | Dependencies for the local launcher. |
| `requirements-sandbox.txt` | Dependencies installed inside the sandbox. |
| `tests/` | Offline tests: the real Responses host with a fake model, API key enforcement, token refresh, sandbox lifecycle with fake clients, and the CLI. |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests require no Azure resources or credentials. They don't prove live sandbox
ingress, egress, or Foundry permissions; use `up` and `invoke` for those checks.
