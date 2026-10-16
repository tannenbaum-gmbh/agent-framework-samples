# Microsoft Learn MCP agent: local and Foundry hosting

A small Agent Framework agent backed by a **Microsoft Foundry model** and connected
to the public [Microsoft Learn MCP server](https://learn.microsoft.com/training/support/mcp).
The goal is to move the **same agent code** between hosting options, not to add a workflow
or custom tools.

| Mode | Where the agent process runs | How to call it | Model / documentation |
| --- | --- | --- | --- |
| Local CLI | Your machine | `python -m app.run_agent` | Foundry / Microsoft Learn MCP |
| Local HTTP host | Your machine | Responses API on port 8088 | Foundry / Microsoft Learn MCP |
| Foundry hosted agent | Python code built and managed by Foundry Agent Service | Authenticated Foundry Responses endpoint or playground | Foundry / Microsoft Learn MCP |

In all modes, **the model remains in Azure**. The agent process calls the model, opens
a Streamable HTTP MCP connection to `https://learn.microsoft.com/api/mcp`, executes
documentation tools requested by the model, and returns an answer with source links.
This is an application-side MCP connection, not a portal-configured Foundry connection
resource. Microsoft Learn's public endpoint requires no API key.

Unlike [`sandbox-hosted-agent`](../sandbox-hosted-agent), this sample deploys a real
**Foundry hosted agent**, not an agent process launched inside a Container Apps sandbox.

## Prerequisites

- Python 3.10+ locally (Foundry uses the managed `python_3_14` runtime).
- A Foundry project and a tool-capable model deployment, such as the GPT-5 deployment
  from [`infra/README.md`](../../infra/README.md). No sandbox group is needed.
- `az login` and permission to invoke the model locally (Foundry User, formerly
  Azure AI User, at project scope).
- Outbound HTTPS access to your Foundry project and `learn.microsoft.com`.
- For deployment: **Foundry Project Manager** at project scope. No Docker,
  Azure Container Registry (ACR), image build/push permissions, or `azd` is required.

The shared infrastructure provides the project/model, **not hosted-agent
deployment permissions**. This sample follows the
[Python ZIP quickstart](https://learn.microsoft.com/azure/foundry/agents/quickstarts/quickstart-hosted-agent?pivots=python).
Check current regional availability and prerequisites in the
[hosted-agent deployment guide](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent).
The Foundry hosting adapter is a beta dependency, pinned here because its API evolves.

## Setup

Run all commands below from this sample directory:

```bash
cd samples/mslearn-hosted-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with the infrastructure outputs, then load it (trusted shell values only).
set -a
source .env
set +a
az login
```

`FOUNDRY_PROJECT_ENDPOINT` is required. Model selection prefers
`FOUNDRY_MODEL_DEPLOYMENT`, then the hosted-runtime variable
`AZURE_AI_MODEL_DEPLOYMENT_NAME`, then `gpt-5`. Environment files are not loaded
automatically.

## 1. Run locally as a CLI

```bash
python -m app.run_agent "How do Foundry hosted agents differ from prompt agents?"
```

`DefaultAzureCredential` uses your local Azure sign-in. The agent context opens and
closes the model client and MCP connection; the credential is also closed on exit.
This mode has no HTTP server and no deployed agent resource.

## 2. Run locally as an HTTP service

```bash
python -m app.host
```

In another terminal:

```bash
curl -f -sS http://localhost:8088/readiness

curl -f -sS http://localhost:8088/responses \
  -H 'Content-Type: application/json' \
  -d '{"input":"What are Foundry hosted agents? Include documentation links.","stream":false,"store":false}'
```

This is the **same Responses host** used in the deployed Python runtime. A factory creates
a fresh agent/client/MCP connection per request; the hosting adapter manages their
async lifecycle. The credential lives for the server lifetime. The example uses
`store:false` for one-shot requests so it needs no conversation persistence setup.

The local listener binds to `0.0.0.0:8088` (override the port with `PORT`). It does
**not** add application authentication: keep it on a trusted development machine,
do not expose or publicly forward it, and stop it with Ctrl+C when finished.
`/readiness` checks the host, not model permissions or MCP connectivity; send a
Responses request to validate those.

## 3. Deploy Python source as a Foundry hosted agent

### Package, upload, wait, and route

Run the deployment command, then invoke the persistent agent:

```bash
python -m app.deploy
python -m app.invoke "When should I use Foundry hosted agents? Include source links."
```

The deployment command:

1. Creates a temporary ZIP containing only `main.py`, `requirements.txt`, and the
   runtime modules in `app/`. Environment files, local credentials, virtual
   environments, tests, and deployment/invocation scripts are not uploaded.
2. Calls `project.agents.create_version_from_code` to create a version of
   `mslearn-agent`, advertising Responses protocol **2.0.0**. Foundry installs
   `requirements.txt` using `CodeDependencyResolution.REMOTE_BUILD` and runs
   `python main.py` in the managed `python_3_14` runtime.
3. Waits up to ten minutes for `active`, then routes **100% of the named agent's
   endpoint traffic to the new version** using `update_details`. Existing endpoint
   routing is replaced only after the new version becomes active.
4. Deletes the local temporary ZIP, including on failure.

Unlike the quickstart's temporary validation script, this sample **keeps the
version and endpoint routing** so you can invoke it later. Re-running deployment
creates another version and switches traffic to it; use a different `--name` to
avoid replacing traffic for an existing agent. Provisioning failures, timeouts,
and routing errors raise exceptions rather than reporting success. Failed versions
remain available for troubleshooting; delete the agent when no longer needed.
There is no separate start operation. Both commands accept `--name`.
Invocations target the named agent's endpoint, not a pinned version.
The invocation command also rejects failed or incomplete Responses results, even
when the server returns HTTP 200.

Foundry builds and runs the uploaded code, exposes an authenticated endpoint, and supplies a dedicated
Entra agent identity. `DefaultAzureCredential` uses that identity inside the runtime;
no developer token or client secret is copied to the ZIP. The deployed version
receives the project endpoint and model deployment as non-secret environment values.
The platform grants its agent identity default model/session access; external Azure
resources require additional RBAC. See
[hosted-agent permissions](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-permissions).

You can also select the deployed agent in the Foundry playground. Local `az login`
does **not** authenticate the deployed runtime. The MCP endpoint is still public,
and must remain reachable from the hosted runtime.

### Cleanup

Delete the sample agent and all its versions when finished:

```bash
python - <<'PY'
import os
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

with DefaultAzureCredential() as credential, AIProjectClient(
    endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=credential
) as project:
    project.agents.delete(agent_name="mslearn-agent")
PY
```

Adjust the name if you deployed with `--name`. Leave shared project/model resources
intact. Hosted compute, remote builds, and model calls may incur charges.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests run offline: configuration, MCP/model wiring, CLI resource cleanup (including
errors), request-scoped hosting, real Responses adapter requests with a fake model,
ZIP contents and entry point, deployment payload/status/routing/error handling,
temporary-file cleanup, and hosted invocation. They do not prove live
Azure permissions, image provisioning, or external MCP availability; use the three
run paths above for those checks.

## Files

| File | Purpose |
| --- | --- |
| `app/agent.py` | Shared Foundry-backed agent and Microsoft Learn MCP tool. |
| `app/config.py` | Shared endpoint and model configuration. |
| `app/run_agent.py` | Local one-shot CLI. |
| `app/host.py` | Responses host, used locally and in Foundry. |
| `app/deploy.py` | Package/upload Python source, wait for readiness, and route the endpoint. |
| `app/invoke.py` | Call the deployed agent's Responses endpoint. |
| `main.py` | ZIP-root entry point that starts the shared HTTP host in Foundry. |

## References

- [Agent Framework Foundry hosting](https://learn.microsoft.com/agent-framework/hosting/foundry-hosted-agent)
- [Hosted agent quickstart (Python ZIP)](https://learn.microsoft.com/azure/foundry/agents/quickstarts/quickstart-hosted-agent?pivots=python)
- [Deploy a Foundry hosted agent](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent)
- [Microsoft Learn MCP server](https://learn.microsoft.com/training/support/mcp)
