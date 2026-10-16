# Architecture review multi-agent workflow

This sample uses **Microsoft Agent Framework** to orchestrate two existing
Foundry prompt agents in three explicit stages:

```text
User request
    |
    v
Enterprise architect: draft       (Foundry Responses API + agent_reference)
    |
    v
Microsoft Learn: independent review (Agent Framework A2AAgent + A2A 1.0)
    |
    v
Enterprise architect: final design  (Foundry Responses API + agent_reference)
```

The architect applies internal architecture guidelines; the documentation
specialist checks Microsoft and Azure product claims using Microsoft Learn MCP.
The final architect call receives the original request, complete draft, and
complete documentation review, including citations.

Unlike the existing
[`enterprise-architect-prompt-agent`](../enterprise-architect-prompt-agent)
sample, where the model decides when to delegate through its configured A2A
tool, this sample makes the **draft, independent review, and reconciliation**
sequence explicit in application code. The architect's existing file-search
and A2A tools remain enabled; it can also consult the documentation agent within
either architect stage. The explicit review is additional, not a replacement
for that grounding.

## Built-in remote-agent integration

The documentation specialist is an
[`A2AAgent`](https://learn.microsoft.com/agent-framework/integrations/by-component/agent-services/a2a?pivots=programming-language-python)
from `agent_framework.a2a`, not a custom JSON-RPC client:

```python
async with (
    httpx.AsyncClient(timeout=120, auth=AzureA2AAuth(credential)) as http,
    A2AAgent(name="documentation", url=a2a_endpoint, http_client=http) as remote,
):
    documentation = DocumentationAgent(remote)
    result = await ArchitectureWorkflow(
        architect.respond, documentation.respond
    ).run(question)
```

Here, `credential`, `architect`, `a2a_endpoint`, and `question` are supplied by
the CLI; see [`app/main.py`](app/main.py) for the complete runnable setup.
`AzureA2AAuth` only supplies Azure authentication. `DocumentationAgent` is a
small completion-policy wrapper: it calls `remote.run(..., background=True)`,
polls with the SDK's `remote.poll_task(...)`, and rejects unsuccessful task
states. Agent Framework and its A2A SDK handle the wire protocol, request IDs,
message serialization, response parsing, and continuation-token creation.

This differs from `FoundryChatClient.get_a2a_tool(...)`: that factory configures
model-driven delegation through a Foundry tool. Here the workflow explicitly
schedules a remote agent as the mandatory documentation-review participant.
The remote agent's instructions, model, and Microsoft Learn MCP tools remain
configured in Foundry.

The requirements pin `agent-framework-a2a==1.0.0b261015`, a prerelease with
A2A 1.0 support, and require Agent Framework core 1.22 or newer plus A2A SDK
1.2.2 or newer. Normal `pip install -r requirements.txt` selects this explicit
prerelease; no global `--pre` option is needed. Tests exercise the real adapter,
not a replacement implementation of A2A.

## Prerequisites

1. Create [`mslearn-prompt-agent`](../mslearn-prompt-agent), including its
   incoming A2A endpoint.
2. Create
   [`enterprise-architect-prompt-agent`](../enterprise-architect-prompt-agent),
   including its uploaded guidelines, A2A connection, and agent-identity role
   assignment.
3. Use the same Foundry project for both agents in this sample.
4. Use Python 3.11 or newer and sign in with `az login`.
5. The local caller needs **Foundry Agent Consumer** or higher on the project
   and permission to invoke the architect through Responses. The architect's
   agent identity separately needs access to the documentation agent, as
   explained in the architect sample.

This sample creates no agents, connections, vector stores, or hosted containers.
It reuses the deployed agent definitions, including their model configuration.
There is no separate model deployment setting or A2A connection to create for
the local caller.

> Internal guidelines in the architect sample are fictional demo policy.
> Only send material to these agents that your organization permits them and
> their tools to process.

## Configure and run

From the repository root:

```bash
cd samples/architecture-review-workflow
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export FOUNDRY_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"

python -m app.main --show-steps \
  "Design a tier-1 Azure application platform. Apply our private networking, recovery, governance, and cost requirements, and validate the Azure capabilities."
```

Alternatively, copy [`.env.example`](.env.example) to `.env`, edit it, and export
its values into your shell. The application does not automatically load `.env`.

| Variable | Required | Default |
| --- | --- | --- |
| `FOUNDRY_PROJECT_ENDPOINT` | Yes | None |
| `ARCHITECT_AGENT_NAME` | No | `enterprise-architect-prompt-agent` |
| `MSLEARN_AGENT_NAME` | No | `mslearn-prompt-agent` |

Omit the question to use the included tier-1 platform request. Omit
`--show-steps` to print only the final recommendation.

For custom deployed names:

```bash
export ARCHITECT_AGENT_NAME="my-enterprise-architect"
export MSLEARN_AGENT_NAME="my-microsoft-docs-agent"
python -m app.main --show-steps
```

The architect's pre-existing A2A connection must also point to the correct
documentation agent if you changed that agent's name.

## Inspect the orchestration

With `--show-steps`, successful output contains three sections:

1. **Architect draft**: proposed components, assumptions, internal requirement
   IDs, and preliminary evidence.
2. **Microsoft Learn A2A review**: supported product claims, corrections,
   limitations, open questions, and Microsoft Learn URLs. This stage does not
   approve internal policy.
3. **Final architecture recommendation**: a reconciled proposal that preserves
   citations and distinguishes internal mandates from public recommendations.

The sequence is deterministic; the model-generated content is not. This is a
single review pass, not an unlimited discussion or an automated architecture
approval gate. Check citations and unresolved questions before relying on the
recommendation.

## Protocol, state, and failures

- Agent Framework executors carry typed per-run messages through a bounded
  workflow. Every run builds fresh executors; no conversation ID or
  `previous_response_id` is shared between requests.
- Architect calls use the existing agent name via `agent_reference` and
  `tool_choice="required"`. They do not recreate its instructions or tools.
- `A2AAgent` connects directly to
  `<project-endpoint>/agents/<name>/endpoint/protocols/a2a` with
  its default JSON-RPC transport and `A2A-Version: 1.0`. With this known endpoint,
  it builds a minimal agent card locally; no remote card discovery is needed.
  `AzureA2AAuth` uses `DefaultAzureCredential` with scope
  `https://ai.azure.com/.default` for every HTTP request, allowing the credential
  to cache or refresh tokens. Application code does not log or persist tokens.
- The SDK handles immediate messages and completed tasks. The workflow wrapper
  polls submitted or working tasks through `poll_task` every two seconds, with
  a 120-second overall A2A timeout covering the initial call and all polls.
  Timing out stops local polling, not the remote task.
- SDK transport and parsing errors, empty responses, incomplete
  architect responses, and failed/canceled/rejected/auth-required/input-required
  tasks raise errors and stop downstream stages. No model-only fallback runs.
  Task state is checked even when an unsuccessful task includes answer text:
  the SDK does not automatically turn all non-completed states into exceptions.
  An authorization URL in task status is preserved in the error; authorize
  access before rerunning, rather than assuming the agent is unavailable.
- Completed model text can still report unavailable evidence or request consent.
  Transport completion is not proof of a grounded or approved design. The final
  architect is instructed to preserve these limitations.
- SDK-generated requests use the GA A2A 1.0 methods and task states also used by
  the documentation sample's smoke test. There is no hand-written JSON-RPC
  serialization or custom protocol fallback.

The remote agent, local credential, project client, Responses client, and HTTP
client are scoped with async context managers on success and failure. The
caller owns and closes the HTTP client passed to `A2AAgent`. Invocation charges
still apply, including additional documentation calls made by the architect.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests run without Azure credentials or network access. They exercise real
Agent Framework workflow execution with fake agent callbacks and the real
`A2AAgent`/A2A SDK with `httpx.MockTransport`, covering ordering, context
forwarding, concurrent-run isolation, the A2A 1.0 wire format, Azure token
acquisition, polling, citations, timeouts, client ownership, and failure
propagation. They do not validate live Azure authorization or model answer
quality.

## Files

| Path | Purpose |
| --- | --- |
| [`app/workflow.py`](app/workflow.py) | Typed draft, review, and finalization executors. |
| [`app/agents.py`](app/agents.py) | Architect Responses adapter, Azure HTTP authentication, and completion policy around the built-in `A2AAgent`. |
| [`app/config.py`](app/config.py) | Project endpoint and deployed agent names. |
| [`app/main.py`](app/main.py) | CLI and asynchronous client lifetimes. |
| [`tests/`](tests) | Deterministic orchestration and adapter tests. |

## Cleanup

There are no additional Azure resources to delete. If you created the two
prerequisite agents only for this demo, follow their respective cleanup
instructions, including vector-store cleanup for the architect.

## References

- [Agent Framework workflows](https://learn.microsoft.com/agent-framework/workflows/)
- [Agent Framework A2A agent integration](https://learn.microsoft.com/agent-framework/integrations/by-component/agent-services/a2a?pivots=programming-language-python)
- [Enable incoming A2A on a Foundry agent](https://learn.microsoft.com/azure/foundry/agents/how-to/enable-agent-to-agent-endpoint)
- [Connect to an A2A agent endpoint](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/agent-to-agent)
