# Sandbox-hosted Agent Framework agent

This sample executes the **Agent Framework process itself** inside an
[Azure Container Apps sandbox](https://learn.microsoft.com/azure/container-apps/sandboxes-overview).
The local process is only a launcher: it creates the sandbox, uploads the agent and its
configuration, starts the remote process, prints its answer, and deletes the sandbox.

This complements [`sandbox-code-agent-evals`](../sandbox-code-agent-evals), where the agent
runs locally and only model-written Python runs in the sandbox.

## Execution flow

1. The launcher acquires a short-lived Entra token for `https://ai.azure.com/.default`.
2. It creates a sandbox from the configured Python disk image.
3. It uploads `app/sandbox_agent.py`, the sandbox dependency manifest, the request, and the token.
4. The sandbox installs Agent Framework and runs the agent process there.
5. The launcher returns stdout and deletes the sandbox in a `finally` block.

The question and token are written as files and never interpolated into a shell command.

## Trust boundary

The uploaded agent is trusted code. The short-lived Foundry token is readable by processes
inside its sandbox, so **do not run model-generated or user-supplied code in that same
sandbox**. Use a second sandbox for untrusted tool execution, as demonstrated by the sibling
sample, or replace token transfer with workload identity when the sandbox service supports
that deployment model.

Sandbox egress must allow package installation from PyPI and HTTPS access to the Foundry
project endpoint. Restrict those destinations with the sandbox group's egress policy for
production use.

## Prerequisites

- Deploy the environment in [`infra/README.md`](../../infra/README.md) with
  `deploySandboxGroup=true`.
- Sign in with `az login`. Your identity needs **Container Apps SandboxGroup Data Owner** on
  the sandbox group and access to invoke the Foundry model deployment.
- Use Python 3.10 or newer locally.

## Run

```bash
cd samples/sandbox-hosted-agent
pip install -r requirements.txt

cp .env.example .env
# Fill in the deployment outputs, then export them using your preferred environment loader.
export $(grep -v '^#' .env | xargs)

python -m app.run_agent "Explain why process isolation matters in two sentences."
```

The first run installs the packages listed in `requirements-sandbox.txt` inside the new
sandbox, so it takes longer than later process startup. For repeated workloads, build a
custom disk image containing those dependencies.

## Files

| File | Purpose |
| --- | --- |
| `app/host.py` | Host-side sandbox provisioning, artifact upload, execution, and cleanup. |
| `app/sandbox_agent.py` | Agent Framework program that executes inside the sandbox. |
| `app/run_agent.py` | Command-line entry point. |
| `requirements.txt` | Dependencies installed on the local launcher. |
| `requirements-sandbox.txt` | Dependencies installed inside the sandbox. |
| `tests/` | Offline lifecycle tests using fake sandbox clients. |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests require no Azure resources or credentials.