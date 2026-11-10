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

- A tenant with Agent 365 enabled, and an Agent ID Developer with a Global
  Administrator available to complete permission grants (or a Global
  Administrator performing setup).
- A supported coding assistant: VS Code agent mode, GitHub Copilot CLI, or
  Claude Code, plus the GitHub CLI (`gh`) to install the skills.
- Deploy the [shared infrastructure](../../infra/README.md#deploying-the-sandbox-group)
  with `deploySandboxGroup=true`. Your signed-in identity needs **Container Apps
  SandboxGroup Data Owner** on the sandbox group and permission to assign
  **Foundry User** on the Foundry account.
- A system-assigned or user-assigned managed identity attached to the sandbox
  group. The sandbox runtime must support managed identity token acquisition
  for `api://AzureADTokenExchange`; attaching an identity alone does not verify it.
- Python 3.11+ locally, Azure CLI sign-in (`az login`), a Foundry project, and a
  tool-capable model deployment (default: `gpt-5.3-codex-2`).
- A Work IQ MCP connection in the Foundry project targeting
  `https://workiq.svc.cloud.microsoft/mcp`, with delegated OAuth configured
  and authorized by each intended user. Follow
  [Connect Work IQ once](../microsoft-enterprise-architect-prompt/README.md#connect-work-iq-once)
  and its [scope and tenant-policy guidance](../microsoft-enterprise-architect-prompt/README.md#optional-action-scope-workiqact-versus-workiqagentask).
- For source-grounded architecture recommendations, upload the fictional
  guidelines described in
  [Upload the internal guidelines](../microsoft-enterprise-architect-prompt/README.md#upload-the-internal-guidelines).

## Set up with Agent 365 skills

Follow [Agent 365 Skills for Guided Agent Setup](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/agent-365-skills)
and its [installation quickstart](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/get-started).
Use the **standard agent / S2S** path, not AI teammate: this sample has no
agent-owned mailbox, Teams presence, or signed-in user for its Foundry calls.

### 1. Prepare the local project

Install the skills, then restart your coding assistant so it discovers them:

```bash
gh skill add microsoft/agent365-skills
```

Prepare this sample's Python environment:

```bash
cd samples/agent365-enterprise-architect
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in the tenant, Foundry endpoint, Azure sandbox settings, and Work IQ connection name.
```

### 2. Register through the guided setup

Open this sample directory in your coding assistant, or explicitly target it:

> Use the a365-setup skill to set up samples/agent365-enterprise-architect
> for Agent 365 as a standard Python agent using S2S authentication.
> Preserve its existing Foundry prompt-agent and Container Apps sandbox runtime.
> Reuse any existing blueprint and agent identity; do not create replacements.

The `a365-setup` skill checks the CLI and Azure prerequisites, then delegates
registration to `make-a365-agent`. Confirm these choices:

- **Agent type:** Standard agent (Agent / Non AI Teammate).
- **Authentication:** S2S; the sandbox calls Foundry as the agent identity.
- **Registration:** Create a blueprint and agent identity only if none exist.
  If already provisioned, supply their IDs and request registration-only setup.
- **Hosting:** Keep the existing sandbox launcher; do not add an AI teammate
  hosting layer or messaging endpoints.
- **Work IQ add-on:** Skip `add-workiq-tools` for this S2S identity. Keep the
  existing delegated Foundry connection described below.
- **Observability:** Optional; see [Activity export](#5-optional-activity-export).

Review the proposed changes before applying them. Complete any administrator
consent handoff and verify successful **Agent Registration**, not just identity
creation, in the setup summary and the Microsoft 365 admin center's **Agents**
view. Preserve the generated local CLI configuration for retries and keep any
credentials out of Git.

### 3. Map the identity and configure sandbox access

Setup records IDs in its local `a365.generated.config.json`; the sample runtime
does not read that file. Copy the confirmed identity values into your local
`.env` using these meanings:

| Sample variable | Value |
| --- | --- |
| `AZURE_TENANT_ID` | Tenant that owns the blueprint and agent identity. |
| `AGENT_BLUEPRINT_APP_ID` | Blueprint application/client ID, not its object ID. |
| `AGENT_IDENTITY_ID` | Agent identity service-principal object ID, used for RBAC. |
| `AGENT_IDENTITY_CLIENT_ID` | Agent identity application/client ID, used for token requests. |
| `AGENT_BLUEPRINT_MANAGED_IDENTITY_CLIENT_ID` | Empty for a system-assigned identity; the attached managed identity's client ID for a user-assigned identity. |

Load the completed settings from this sample directory:

```bash
# Only source a trusted local file. Quote values containing spaces or shell operators.
set -a
source .env
set +a
```

Agent 365 registration does not configure sandbox federation or Foundry RBAC.
Trust the sandbox group's managed identity on the registered blueprint using
the included management command (skip if this trust already exists):

```bash
python -m app.manage_identity blueprint add-managed-identity \
  --principal-id <sandbox-managed-identity-object-id>
```

This command uses delegated administrator sign-in. The federated credential
uses the managed identity's **object/principal ID**, whereas the runtime
variable above uses its **client ID**. The sandbox never receives a blueprint
client secret. See the [identity sample's credential guidance](../agent365-identity-agent/README.md#2-give-the-blueprint-a-credential)
for local management credentials and federation troubleshooting.

Grant the agent identity access to the specific Foundry account containing
your project (skip if the assignment already exists):

```bash
FOUNDRY_ID=$(az cognitiveservices account show \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name <foundry-account-name> --query id -o tsv)
az role assignment create \
  --assignee-object-id "$AGENT_IDENTITY_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Foundry User" \
  --scope "$FOUNDRY_ID"
```

Allow time for role propagation. Your local signed-in identity also needs
Foundry project access to create and delete prompt-agent versions.

### 4. Connect Work IQ and create the Foundry agent

Set `WORKIQ_CONNECTION_NAME` to the existing Foundry project connection
configured in the prerequisites. Secrets stay in that connection, not in this
sample. The skills skip Work IQ tooling for S2S, but this sample deliberately
uses a separate **delegated OAuth** connection for remote Work IQ calls. Do not
change the agent identity to OBO or assume S2S registration grants access to
Microsoft 365 data.

Reload your trusted `.env` after any edits, then create a versioned agent definition:

```bash
python -m app.create
```

Creation verifies that the named project connection targets the Work IQ MCP
endpoint, then creates a prompt-agent version with the Microsoft Learn and
Work IQ tools. It prints the immutable version identifier.

### 5. Optional Activity export

This sample already includes opt-in instrumentation. Ask your coding assistant:

> Use a365-code-validator to validate this sample's existing Agent 365
> observability integration in read-only mode. It is a standard S2S agent
> executing in a Container Apps sandbox. Preserve managed-identity federation.

Use `instrument-observability` only if you need to add missing instrumentation,
and review its changes rather than installing a second exporter. Registration
alone does not emit Activity telemetry. Observability requires at least one
user in the tenant with a Microsoft 365 E7 or Microsoft Agent 365 license assigned.

To enable the existing exporter, set `ENABLE_A365_OBSERVABILITY_EXPORTER=true`
and populate `A365_SPONSOR_ID`, `A365_SPONSOR_NAME`, and `A365_SPONSOR_EMAIL`
with the actual blueprint sponsor's details in your local `.env`, then reload it.
Keep `A365_CAPTURE_CONTENT=false` unless content capture is approved. After
a run, verify Activity in the Microsoft 365 admin center; a successful export
response alone does not prove Activity ingestion.

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
