# Sandboxed code agent + Microsoft Foundry evaluations

An [Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python)
agent that answers quantitative questions by **writing Python and running it inside an
[Azure Container Apps sandbox](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-overview)**,
and a scored evaluation of that agent using **Microsoft Foundry's evaluation service**.

The sample shows two things that belong together: giving an agent a genuinely dangerous
capability (arbitrary code execution) in a safely isolated place, and then proving with
evaluations that it actually uses that capability correctly.

## What it demonstrates

**Container Apps Sandboxes (preview)** — Each sandbox is a Hyper-V-isolated microVM with
its own filesystem, process space, and network egress controls, created in under a second
from a public disk image. Model-written code never runs on the host that runs this sample.
`app/sandbox.py` creates one sandbox lazily, reuses it across tool calls (so files written
in one step survive to the next, like a notebook kernel), and deletes it when finished.

**Foundry evaluations** — `app/evaluate.py` calls Agent Framework's `evaluate_agent()` with
a `FoundryEvals` evaluator bundle. Every question in `app/dataset.py` is actually run
through the agent — really executing code in the sandbox — and the resulting conversations,
including tool calls, are scored by Foundry's built-in evaluators:

| Evaluator | Question it answers |
| --- | --- |
| `task_adherence` | Did the agent do what the user asked, end to end? |
| `tool_call_accuracy` | Did it call `run_python` with sensible code instead of guessing? |
| `tool_output_utilization` | Did it actually use the sandbox output in its answer? |
| `relevance` | Is the final answer an on-topic response to the question? |

The run is published to the Foundry portal — the console prints a `report_url` — and the
script exits non-zero when any score falls below the threshold, so it works as a CI gate.

## Files

| File | Description |
| --- | --- |
| `app/config.py` | Foundry and sandbox settings, read from environment variables. |
| `app/sandbox.py` | `SandboxCodeRunner`: sandbox lifecycle and Python execution. |
| `app/agent.py` | The agent and its `run_python` function tool. |
| `app/dataset.py` | Evaluation tasks with ground-truth answers and expected tool calls. |
| `app/evaluate.py` | Runs the evaluation against Foundry and enforces the quality gate. |
| `app/run_agent.py` | Ask the agent a single question, to watch the sandbox work. |
| `tests/` | Offline unit tests using fakes — no Azure resources required. |

## Prerequisites

- The Foundry environment from [`infra/README.md`](../../infra/README.md), deployed with
  `deploySandboxGroup=true` so a `Microsoft.App/sandboxGroups` resource is created and your
  identity gets the **Container Apps SandboxGroup Data Owner** role on it.
- `az login` with that identity. The sample authenticates with `DefaultAzureCredential`.
- Python 3.10+.

> Container Apps Sandboxes are in preview and available in a subset of regions. Check the
> [overview](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-overview) for
> current availability before choosing `AZURE_SANDBOX_REGION`.

## Run it

```bash
cd samples/sandbox-code-agent-evals
pip install -r requirements.txt

cp .env.example .env    # fill in the infra outputs
export $(grep -v '^#' .env | xargs)

# Ask a single question and watch the agent compute it in the sandbox
python -m app.run_agent "How many primes are below 1,000,000?"

# Run the full evaluation and publish it to Microsoft Foundry
python -m app.evaluate
```

`python -m app.evaluate` prints something like:

```
5/5 evaluation items passed
  - relevance: 4.8
  - task_adherence: 4.6
  - tool_call_accuracy: 5.0
  - tool_output_utilization: 4.4
  View the full run in Microsoft Foundry: https://ai.azure.com/...

Quality gate passed.
```

Open the report URL to inspect each conversation, the generated Python, the sandbox output,
and the per-evaluator reasoning in the Foundry portal.

## Configuration

| Variable | Description |
| --- | --- |
| `FOUNDRY_PROJECT_ENDPOINT` | AI Foundry project endpoint (infra output `foundryProjectEndpoint`). |
| `FOUNDRY_MODEL_DEPLOYMENT` | Model deployment name. Defaults to `gpt-5`. |
| `AZURE_SUBSCRIPTION_ID` | Subscription holding the sandbox group. |
| `AZURE_RESOURCE_GROUP` | Resource group holding the sandbox group. |
| `AZURE_SANDBOX_GROUP` | Sandbox group name (infra output `sandboxGroupName`). |
| `AZURE_SANDBOX_REGION` | Sandbox group region (infra output `sandboxGroupLocation`). |
| `SANDBOX_DISK_IMAGE` | Public disk image for new sandboxes. Defaults to `python-3.14`. |

To see which disk images are currently published, call
`SandboxGroupClient.list_public_disk_images()`.

## Tests

The tests replace the sandbox client, the chat client, and `evaluate_agent` with fakes, so
they run offline and need no Azure credentials:

```bash
pip install -r requirements-dev.txt
pytest
```

They cover the sandbox lifecycle (one sandbox created and reused, even under concurrent tool
calls), the execution contract (code is written to a file rather than interpolated into a
shell command, temporary scripts are cleaned up, long output is truncated, failures are
reported back so the model can retry), the tool schema exposed to the model, and the
evaluation wiring including the quality gate.

## How the sandbox tool works

1. The model calls `run_python` with a snippet.
2. `SandboxCodeRunner` creates the sandbox on first use, reusing it afterwards.
3. The snippet is **written to a file** in the sandbox and executed as `python3 <path>` —
   never interpolated into the command string, because `exec` runs a shell and the snippet
   is untrusted model output.
4. Exit code, stdout, and stderr are truncated and returned to the model, so it can read
   errors and fix its own code.
5. The sandbox is deleted when the run finishes.

## Notes

- `evaluate_agent`, `EvalResults`, and `FoundryEvals` are marked experimental in Agent
  Framework and may change.
- Sandboxes are billed while they exist; both entry points delete theirs in a `finally`
  block. Sandboxes also auto-suspend after five minutes of inactivity by default.
