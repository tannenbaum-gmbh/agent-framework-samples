# Microsoft Enterprise Architect Hosted

A separate **Foundry hosted agent** with the same sources as
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

```text
CLI / Foundry playground / published Teams personal chat
                         |
          Foundry hosted Python Responses server
                         |
           Agent Framework + FoundryChatClient
                         |
    Microsoft Learn MCP + Foundry Toolbox -> Work IQ MCP
                         |
    SharePoint / OneDrive / Mail / Teams / Calendar / Agents
```

Microsoft Learn is a public, model-side `MCPTool`. Work IQ is exposed to the host
through a dedicated Foundry Toolbox, backed by an existing delegated connection
to `https://workiq.svc.cloud.microsoft/mcp`. Both are configured with
`require_approval="never"` and no `allowed_tools` restrictions. The full live catalogs are exposed;
no client-side allowlist freezes the available tools. Use live schemas rather
than assuming resource paths or supported operations.

Explicitly requested, authorized writes and actions can execute without confirmation prompts.
Work IQ is required; there is no private-tools opt-out. Use only a disposable
tenant and synthetic data. OAuth consent, source permissions, administrator
consent, and tenant policy still apply. Work IQ blocks mutations by default;
exposing a tool does not prove that the tenant allows it.

**Why the hosted path differs:** the prompt agent's tool is executed as part of
a Foundry invocation that already has the signed-in caller. The old hosted
sample put Work IQ on a separate model call authenticated as the host. Passing a
project connection ID (or forwarding the call ID only to the model endpoint)
did not establish caller-bound Work IQ authorization at that boundary.

The hosting SDK's `FoundryToolbox` forwards the current opaque call ID on
toolbox MCP requests. The host credential authenticates to Foundry; the
toolbox resolves the signed-in caller and applies the downstream delegated
connection. The runtime rejects turns missing the platform user ID or call ID.
It never retries Work IQ as the host or another user. No user token or OAuth
client secret is packaged, and model response storage remains disabled.

## Prerequisites and connections

- Python 3.10+ locally; the managed source deployment uses `python_3_14`.
- A Foundry project and a model deployment supporting MCP. Use the actual
  deployment name; the default is `gpt-5.3-codex-2`. The shared
  [`infra/`](../../infra) supplies the project/model, not hosted-agent
  deployment permissions.
- `az login`; **Foundry Project Manager** at project scope for deployment and
  ARM read access to the Work IQ project connection for deployment. Check
  current invocation roles and availability in
  the [hosted-agent source deployment guide](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent-code).
- Outbound access from the hosted runtime to Foundry and the MCP services.
- A disposable Microsoft 365 workshop tenant, synthetic data, two users, and an
  administrator for consent, source permissions, billing, policy, and Teams
  distribution.

Reuse the prompt sample's setup in the **same Foundry project**:

1. [Connect Work IQ](../microsoft-enterprise-architect-prompt/README.md#connect-work-iq-once)
   targeting `https://workiq.svc.cloud.microsoft/mcp`, not preview A2A.
   For native Teams publishing and a first-use Work IQ consent screen, use
   the `OAuth2` connection setup in that guide. This is per-user OAuth
   passthrough, not Entra's `AgentOnBehalfOfUser` token exchange.
   For literal Entra OBO, use a separate `AgentOnBehalfOfUser` connection
   with `api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask`, configure the
   [agent blueprint endpoint and delegated consent](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-user-delegation),
   and invoke with a blueprint-audience user token. Teams publishing's Bot
   Service authorization alone does not establish blueprint access. Use
   `OAuth2` for the Teams pilot unless blueprint delegation is verified on
   that surface. Entra consent may already have been granted by an admin.
2. Verify the full Work IQ catalog, including SharePoint retrieval. Configure
   only the supported mutation exercises needed in the disposable tenant
   using [Work IQ policy governance](https://learn.microsoft.com/microsoft-365/copilot/extensibility/work-iq/mcp/policy-governance-mcp).
   Do not broadly enable writes or bypass tenant-policy denials.
3. [Upload the fictional guidelines](../microsoft-enterprise-architect-prompt/README.md#upload-the-internal-guidelines)
   from [`../enterprise-architect-prompt-agent/internal-guidelines/`](../enterprise-architect-prompt-agent/internal-guidelines/)
   to your disposable SharePoint site. Preserve filenames and SEC/REL/GOV/COST
   IDs; give participants read access and use a controlled output folder.
   Deployment does not upload documents or create a vector store.

Deployment creates the Work IQ toolbox; no separate SharePoint connection is needed. A connection configured
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

Deployment requires `WORKIQ_CONNECTION_NAME`. Before uploading, it verifies the
connection's MCP target and reads its ARM auth type (not its credentials).
Anything other than `OAuth2` or `AgentOnBehalfOfUser` is rejected. It creates a
version of `<agent-name>-workiq` with that connection and pins the hosted
runtime to that version. Microsoft Learn needs no connection.

Only `main.py`, `requirements.txt`, `app/__init__.py`, `app/agent.py`,
`app/config.py`, and `app/host.py` are packaged. Foundry installs dependencies
remotely, creates a **hosted** version advertising Responses **2.0.0**, and runs
`python main.py`. The deployer waits up to ten minutes for `active`, then routes
100% of the named endpoint to that version. Existing protocol settings and
authorization schemes, including Teams publishing, are preserved. No routing
change happens on provisioning failure. Failed versions remain for
troubleshooting; the temporary local ZIP is always removed.

The runtime receives only non-secret endpoint/model values and
`WORKIQ_TOOLBOX_NAME` and `WORKIQ_TOOLBOX_VERSION`. A bare local Responses
request does not have Foundry's signed-in caller context; use a deployed
endpoint to exercise Work IQ. Model selection
prefers `FOUNDRY_MODEL_DEPLOYMENT`, then `AZURE_AI_MODEL_DEPLOYMENT_NAME`, then
the sample default.

For migration, remove any old `WORKIQ_CONNECTION_ID`, set
`WORKIQ_CONNECTION_NAME`, and redeploy. If anyone may have received another
user's data, disable the affected Teams distribution now and follow your
incident-response process. Use a separate agent name for a synthetic-data
pilot rather than immediately routing the existing Teams endpoint to the fix.

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

### Verify Agent 365 tool telemetry

The sample uses the Foundry host's existing OpenTelemetry configuration. It
adds an `execute_tool` span when the model response reports a completed or
failed Microsoft Learn MCP call. Work IQ calls now execute as Agent Framework
tools through the toolbox, not as model-side `mcp_call` items. The Microsoft
Learn span includes the tool name, type, and call ID,
not the tool arguments, results, or error text. Its duration is not the remote
tool's execution time. Work IQ execution and delegated OAuth stay with Foundry.

With [Agent 365 observability configured](https://learn.microsoft.com/azure/foundry/agents/how-to/grant-agent-365-permissions),
deploy the updated version and ask the published agent to verify a public Azure
fact using Microsoft Learn MCP. First check the Foundry trace for `execute_tool`,
then check the agent's Activity and Single Agent Map in the Microsoft 365 admin
center. If the span exists but Activity is missing, check the hosted runtime's
Agent 365 exporter logs before changing tool instrumentation.

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

**Release gate:** In two Teams personal chats, use fresh synthetic accounts A
and B. Give each an exclusive test message. Have A consent and retrieve A's
message. Before B consents, confirm B gets a consent prompt or a denial, not
A's data. After B consents, verify each can retrieve only their own message and
that explicit cross-user reads fail in both directions. Repeat in existing
chats and after reconnecting. If consent or caller isolation fails, do not
distribute. Keep private data in personal chats, not groups/channels.

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

This leaves shared project/model resources, connections, toolbox versions,
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
