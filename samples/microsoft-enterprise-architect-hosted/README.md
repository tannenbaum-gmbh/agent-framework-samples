# Microsoft Enterprise Architect Hosted

A separate **Foundry hosted agent** using the same direct MCP pattern as
[`microsoft-enterprise-architect-prompt`](../microsoft-enterprise-architect-prompt):
**Work IQ MCP provides all Microsoft 365 tools**, including SharePoint and
OneDrive documents, mail, Teams, calendars, and available agents.
**Microsoft Learn MCP** verifies Azure guidance against official documentation.
There is no toolbox, separate SharePoint MCP, or web-search tool.

Unlike the prompt sample, **your Python code owns the Agent Framework agent**.
Foundry builds the uploaded source ZIP and runs the Responses host on managed
compute. No Docker, container registry, custom bot host, or prompt-agent
deployment is required. The existing prompt agent is not changed or invoked.
The default resource name is `mea-hosted-agent`; the display name for publishing
is **Microsoft Enterprise Architect Hosted**.

For the same architecture hosted **outside Foundry**, see
[`microsoft-enterprise-architect-sandbox`](../microsoft-enterprise-architect-sandbox):
an authenticated Responses API service in the existing ACA sandbox group,
with direct MCP clients and a separate delegated Work IQ sign-in.

```text
CLI / Foundry playground / published Teams personal chat
                         |
          Foundry hosted Python Responses server
                         |
           Agent Framework + FoundryChatClient
                         |
         Microsoft Learn MCP + Work IQ MCP
                         |
    SharePoint / OneDrive / Mail / Teams / Calendar / Agents
```

Both tools are native Foundry-managed `MCPTool` configurations with
`require_approval="never"` and no `allowed_tools` restrictions. Microsoft Learn
uses `https://learn.microsoft.com/api/mcp` without an authenticated connection.
Work IQ uses `https://workiq.svc.cloud.microsoft/mcp` and an existing Foundry
project connection for delegated OAuth. The full live catalogs are exposed;
no client-side allowlist freezes the available tools. Use live schemas rather
than assuming resource paths or supported operations.

**Behavior change:** the former toolbox, web search, read-only work-context
instructions, and interactive approvals are removed. Explicitly requested,
authorized writes and actions can execute without confirmation prompts.
Work IQ is required; there is no private-tools opt-out. Use only a disposable
tenant and synthetic data. OAuth consent, source permissions, administrator
consent, and tenant policy still apply. Work IQ blocks mutations by default;
exposing a tool does not prove that the tenant allows it.

The request-scoped factory creates fresh model clients and forwards the hosting
SDK's first-party platform headers to Foundry. The opaque call ID lets Foundry
resolve caller context; no participant token or client secret is copied into
the application or ZIP. Foundry manages MCP execution and connection
authentication, not a local toolbox client. Model response storage is disabled
because the hosting infrastructure manages conversation history. The platform
agent identity authenticates the model call; it does **not** substitute for each
user's Microsoft 365 consent.

## Prerequisites and connections

- Python 3.10+ locally; the managed source deployment uses `python_3_14`.
- A Foundry project and a model deployment supporting MCP. Use the actual
  deployment name; the default is `gpt-5.3-codex-2`. The shared
  [`infra/`](../../infra) supplies the project/model, not hosted-agent
  deployment permissions.
- `az login`; **Foundry Project Manager** at project scope for deployment and
  **Foundry User** for invocation. Check current availability and permissions in
  the [hosted-agent source deployment guide](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent-code).
- Outbound access from the hosted runtime to Foundry and the MCP services.
- A disposable Microsoft 365 workshop tenant, synthetic data, two users, and an
  administrator for consent, source permissions, billing, policy, and Teams
  distribution.

Reuse the prompt sample's setup in the **same Foundry project**:

1. [Connect Work IQ](../microsoft-enterprise-architect-prompt/README.md#connect-work-iq-once)
   targeting `https://workiq.svc.cloud.microsoft/mcp`, not preview A2A.
   Reuse the delegated OAuth connection, normally `workshop-workiq-mcp`.
   Follow the linked guide's exact baseline OAuth scopes and optional
   `WorkIQ.Act` permission expansion; Azure CLI login is not Microsoft 365
   OAuth consent. Complete consent as each intended user.
2. Verify the full Work IQ catalog, including SharePoint retrieval. Configure
   only the supported mutation exercises needed in the disposable tenant
   using [Work IQ policy governance](https://learn.microsoft.com/microsoft-365/copilot/extensibility/work-iq/mcp/policy-governance-mcp).
   Do not broadly enable writes or bypass tenant-policy denials.
3. [Upload the fictional guidelines](../microsoft-enterprise-architect-prompt/README.md#upload-the-internal-guidelines)
   from [`../enterprise-architect-prompt-agent/internal-guidelines/`](../enterprise-architect-prompt-agent/internal-guidelines/)
   to your disposable SharePoint site. Preserve filenames and SEC/REL/GOV/COST
   IDs; give participants read access and use a controlled output folder.
   Deployment does not upload documents or create a vector store.

No toolbox or separate SharePoint connection is needed. A connection configured
only in Copilot Studio is not automatically a Foundry connection. Missing
consent, permissions, billing, or unsupported schemas must be resolved at the
source; the code never substitutes fixtures, another user, or another server.

## Configure and deploy

Run from this directory:

```bash
cd samples/microsoft-enterprise-architect-hosted
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit the endpoint, model deployment, and Work IQ connection name.
set -a
source .env
set +a
az login

python -m app.deploy
python -m app.invoke
```

Environment files are not loaded automatically; source only trusted shell values.
No tokens or secrets belong in `.env`.

Deployment requires `WORKIQ_CONNECTION_NAME`. It resolves that connection in
the configured project, validates its MCP target and nonempty ID before upload,
and passes the resolved ID as `WORKIQ_CONNECTION_ID` to the hosted runtime.
It does not retrieve connection credentials or change connection permissions.
Microsoft Learn needs no connection.

Only `main.py`, `requirements.txt`, `app/__init__.py`, `app/agent.py`,
`app/config.py`, and `app/host.py` are packaged. Foundry installs dependencies
remotely, creates a **hosted** version advertising Responses **2.0.0**, and runs
`python main.py`. The deployer waits up to ten minutes for `active`, then routes
100% of the named endpoint to that version. Existing protocol settings and
authorization schemes, including Teams publishing, are preserved. No routing
change happens on provisioning failure. Failed versions remain for
troubleshooting; the temporary local ZIP is always removed.

The runtime receives only non-secret endpoint/model values and
`WORKIQ_CONNECTION_ID`; a missing ID fails explicitly. If starting `python main.py`
locally, also set that ID to the existing connection's full ID. Model selection
prefers `FOUNDRY_MODEL_DEPLOYMENT`, then `AZURE_AI_MODEL_DEPLOYMENT_NAME`, then
the sample default.

For migration, replace `TOOLBOX_NAME`/`TOOLBOX_VERSION` in your local environment
with `WORKIQ_CONNECTION_NAME` and redeploy. Toolbox settings are no longer read.
The old `--without-toolbox`, `--without-workiq`, and `--review-m365-tools` flags
are removed. The shared toolbox is neither used nor deleted.

Rerunning deployment creates another version and switches endpoint traffic.
Use `--name custom-mea-hosted` on **deploy, invoke, and delete** to isolate
another deployment. Invocation uses the named hosted endpoint, not
`agent_reference` or the prompt sample's `--version` option.

## Invoke

```bash
python -m app.invoke \
  "Use the guidelines in <guideline-folder-link> on <site-url> to design supplier invoice automation. Cite internal requirement IDs and Microsoft documentation."
python -m app.invoke \
  "Read the requirements from my test Teams chat <chat-id>, message <message-id>, and propose an architecture."
python -m app.invoke \
  "Read my workshop architecture review meeting agenda. Do not modify my calendar."
python -m app.invoke \
  "In <site-url>, library <library-id>, folder <folder-id>, check that architecture-review-unique.md does not exist. Create that NEW Markdown file with this synthetic content: Invoice automation review; owner Workshop Team. Read it back. Never overwrite."
```

Calls do not require an interactive terminal or tool-use approval. Unexpected
MCP approval requests fail with a redeployment instruction rather than being
silently approved. Failed/incomplete responses, MCP errors, and OAuth consent
requests are reported explicitly. Complete consent in the trusted Foundry
playground as the intended participant and retry; verify private reads from
the hosted endpoint before claiming Microsoft 365 connectivity.

Each CLI invocation starts a **fresh response chain** using `store:true` for
host-managed state. Response and session state can persist under Foundry's
retention settings; this is not the prompt sample's create/delete-conversation
lifecycle. Never share response IDs or user sessions. `--debug` exposes full
responses and potentially private tool data.

Use exact live schemas for entities, actions, functions, and binary reads.
Do not claim to parse PDF/DOCX content without readable evidence. For new
files, conflict checks and no-overwrite rules are model instructions, not
atomic create-only enforcement. A partial folder listing is not proof of no
conflict. Use a verified new filename in a controlled folder and read it back.

## Workshop, safety, and Teams

Reuse the prompt sample's [facilitator guide](../microsoft-enterprise-architect-prompt/WORKSHOP.md)
and [synthetic fixtures](../microsoft-enterprise-architect-prompt/fixtures).
Run retrieval, requested changes, tenant-policy denial, confidentiality, and
prompt-injection exercises against `mea-hosted-agent`. Replace prompt creation
commands with `app.deploy` and omit `--version` from CLI invocations. Record
the **hosted version**, user, surface, source permissions, and actual tool
traces. The prompt agent does not need to be deployed.

For Teams, follow the current
[Foundry native publishing guide](https://learn.microsoft.com/azure/foundry/agents/how-to/publish-copilot):
open **mea-hosted-agent**, select the tested active version, and choose
**Publish > Teams and Microsoft Copilot**. Use **Microsoft Enterprise Architect
Hosted** and a description stating full Microsoft 365 work-context access and
explicitly requested actions without per-call approvals. Start with **Just
you**, then obtain tenant approval before organization-wide distribution.
Foundry handles bot registration; adding the agent to Teams grants no mailbox
or SharePoint access. For private-network projects, use the publishing guide's
REST path without opening account-wide public access.

Verify delegated consent, distinct participant contexts, authorized operations,
and tenant-policy denials on the hosted playground/Teams surface before
distribution. If a surface cannot present delegated consent, record the
limitation rather than publishing it as working. Keep private data in personal
chats, not groups/channels. Mermaid may appear as code in Teams.

Confidentiality, injection resistance, and no-overwrite rules are model
instructions, **not DLP or authorization enforcement**. Rely on source
permissions, tenant policy, consent, and real protections. Never send private
content to public tools. Use synthetic data only. A refusal is not proof of
source enforcement; record unavailable live checks as **not verified**.

## Cleanup

Remove published Teams distribution first, then delete this sample's agent
**and all its versions**, including failed builds and endpoint routing:

```bash
python -m app.delete
# For a custom deployment:
python -m app.delete --name custom-mea-hosted
```

This leaves shared project/model resources, connections, any existing toolbox,
the prompt agent, and SharePoint content intact. Manually remove only
workshop-created files. Hosted compute, builds, model calls, and M365 tools may
incur charges.

## Offline checks

```bash
pip install -r requirements-dev.txt
pytest
```

Tests cover direct MCP payloads with full catalogs and no per-call approvals,
connection resolution/validation, ZIP contents, hosted deployment/routing and
failure handling, published protocol preservation, resource cleanup, CLI
error reporting, and the real Responses host and Foundry chat client's
MCP serialization, caller-context forwarding, and connection cleanup using
mocked HTTP. They do **not** verify live provisioning, consent/billing, source
permissions, model safety, MCP availability, or Teams publishing. Those require
the live workshop checks above.
