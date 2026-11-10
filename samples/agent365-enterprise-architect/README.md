# Agent 365 Enterprise Architect

This sample combines the Agent 365 identity setup from
[`agent365-identity-agent`](../agent365-identity-agent) with the architecture
instructions, Microsoft Learn MCP, and full Work IQ MCP catalog from
[`microsoft-enterprise-architect-prompt`](../microsoft-enterprise-architect-prompt).
It creates a versioned Foundry prompt agent and invokes it from an ephemeral
Container Apps sandbox using an Entra agent identity.

```text
local CLI ──run trusted code + non-secret settings──▶ Container Apps sandbox
                                                       │
                           agent identity token ───────┤
                                                       ▼
                                                   Foundry API
                                                       │
                                      versioned prompt agent
                                        │             │
                              Microsoft Learn MCP   Work IQ MCP
                                                      │
                                          delegated user OAuth
```

**The two credentials are deliberately separate.** The sandbox uses its Agent
365 identity to authenticate the request to Foundry. Work IQ uses the
delegated OAuth connection configured in the Foundry project; the agent
identity does not impersonate a user and does not grant access to that user's
Microsoft 365 data. Microsoft Learn MCP is public. The MCP catalogs have
`require_approval="never"`; that removes per-call prompts, not OAuth consent,
source permissions, or tenant policy. Use only a disposable tenant and
synthetic data.

## Prerequisites

- Follow [`agent365-identity-agent` prerequisites](../agent365-identity-agent/README.md#prerequisites),
  including Foundry User access for the agent identity and an Azure Container
  Apps sandbox group.
- Python 3.11+ locally, Azure CLI sign-in, a Foundry project, and a
  tool-capable model deployment (default: `gpt-5.3-codex-2`).
- A Work IQ MCP connection in the Foundry project targeting
  `https://workiq.svc.cloud.microsoft/mcp`, with delegated OAuth configured
  and authorized by each intended user. Follow
  [Connect Work IQ once](../microsoft-enterprise-architect-prompt/README.md#connect-work-iq-once)
  and its [scope and tenant-policy guidance](../microsoft-enterprise-architect-prompt/README.md#optional-action-scope-workiqact-versus-workiqagentask).
- For source-grounded architecture recommendations, upload the fictional
  guidelines described in
  [Upload the internal guidelines](../microsoft-enterprise-architect-prompt/README.md#upload-the-internal-guidelines).

## Configure identity and Foundry

Use the identity provisioning, managed-identity federation, Foundry role
assignment, and optional Agent 365 registration steps in the
[`agent365-identity-agent` README](../agent365-identity-agent/README.md). Run
its `app.manage_identity` commands from this sample directory so they use the
configuration below. The provisioning commands and identity management code
are included here as well.

```bash
cd samples/agent365-enterprise-architect
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set the identity IDs, Foundry endpoint, Azure sandbox settings, and Work IQ connection name.
set -a
source .env
set +a
```

The `.env` variables for identity and sandbox configuration have the same
meaning as in `agent365-identity-agent`. `WORKIQ_CONNECTION_NAME` is the name
of the existing Foundry project connection; secrets stay in that Foundry
connection, not in this sample.

Create a versioned agent definition:

```bash
python -m app.create
```

Creation verifies that the named project connection targets the Work IQ MCP
endpoint, then creates a prompt-agent version with the Microsoft Learn and
Work IQ tools. It prints the immutable version identifier.

## Run

```bash
python -m app.run_agent --version <version>
python -m app.run_agent --version <version> \
  "Design an Azure architecture for supplier invoice automation. Apply our internal guidelines and cite current Microsoft documentation."
```

The launcher creates an ephemeral sandbox, uploads the trusted runtime and
non-secret request configuration, and removes the sandbox after the run. The
agent identity token is requested for `https://ai.azure.com/.default`, checked
against the configured agent identity and blueprint using the Agent 365 SDK,
and used to authenticate the Foundry API call. Each prompt-agent invocation
uses a fresh conversation, reports tool errors or unexpected approval
requests, and deletes that conversation in a `finally` block.

Work IQ tools run through the Foundry project connection and its delegated
OAuth grant. The OAuth user and their source permissions—not the Agent 365
identity—determine Microsoft 365 data access. For OAuth consent, use the
trusted consent flow surfaced by Work IQ and sign in as the intended user.
Keep Work IQ policy scoped to workshop operations; the tools include
mutations, and auto-approved calls do not provide an argument-review step.
Never use production mailboxes, calendars, or document libraries.

The prompt-agent instructions are model guidance, not deterministic
authorization or DLP controls. Read-only retrieval can disclose accessible
private data. Do not send private content to public tools, and treat retrieved
content as untrusted reference data. Check tenant policy and source
permissions independently.

Optional Agent 365 Activity export uses the sample's S2S telemetry credential
and sponsor settings. Its spans cover the sandbox invocation and remote
inference request; inspect Foundry/MCP traces separately for server-side tool
execution. Keep content capture disabled unless prompts and responses are
approved for telemetry storage.

## Cleanup

Delete only the version created for this sample:

```bash
python -m app.delete --version <version>
```

Deleting the version does not remove its Work IQ connection, OAuth consent,
agent identity, blueprint, sandbox group, or any workshop documents. Use the
identity sample's management commands to disable or delete the agent identity
only when it is no longer needed.

## Local checks

```bash
pip install -r requirements-dev.txt
pytest
```

Tests cover identity token acquisition and claims, MCP tool configuration,
Work IQ connection validation, isolated prompt invocation, and cleanup. They
do not prove live consent, source access, tenant policy, MCP retrieval, or
Teams publishing. This sample does not publish to Teams; use the prompt
sample's [native Teams publishing guide](../microsoft-enterprise-architect-prompt/README.md#integrate-with-microsoft-teams)
if you need a Teams chat surface.
