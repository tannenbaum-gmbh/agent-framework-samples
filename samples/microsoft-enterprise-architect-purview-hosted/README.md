# Microsoft Enterprise Architect Purview Hosted

A copy of [`microsoft-enterprise-architect-hosted`](../microsoft-enterprise-architect-hosted)
with the **Microsoft Purview SDK** (`agent-framework-purview`) integrated using
the [Agent Framework Python middleware](https://learn.microsoft.com/agent-framework/integrations/by-component/middleware/purview?pivots=programming-language-python).
The original sample is unchanged. This copy uses its own deployment name,
`mea-purview-hosted-agent`, and mandatory, fail-closed Purview checks.

The code-owned Agent Framework agent still uses `FoundryChatClient`, Microsoft
Learn MCP, and the full Work IQ MCP catalog (SharePoint, OneDrive, mail, Teams,
calendar, and available agents). It still deploys Python source as a ZIP to a
Foundry Responses host, without Docker or a container registry. Tool calls use
`require_approval="never"`; source permissions, delegated consent, and tenant
policy still apply. Explicitly requested, authorized writes need no per-call
approval. Use a disposable tenant and synthetic data.

```text
Workshop user -> Foundry hosted Responses server
             -> Trusted user binding -> Purview chat middleware
             -> FoundryChatClient -> Foundry-managed Learn / Work IQ MCP
             -> Purview response evaluation -> caller
```

**Single-user workshop only:** app-only checks are attributed to the trusted
`PURVIEW_USER_ID` configured by the operator, not each Foundry/Teams caller.
Restrict invocation to that workshop user. Do not distribute this fixed-user
variant to multiple participants. A multi-user deployment requires
server-validated caller identity and per-request Purview authentication/binding.
Work IQ's delegated OAuth remains separate and unchanged.

## Prerequisites

- Python 3.11+ locally; managed deployment uses `python_3_14`.
- A Foundry project and an MCP-capable model deployment (default
  `gpt-5.3-codex-2`). Shared [`infra/`](../../infra) supplies the project/model.
- `az login`, Foundry Project Manager for deployment, and Foundry User for
  invocation. See the [hosted-agent source deployment guide](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent-code).
- Runtime outbound access to Foundry, Microsoft Graph, and MCP endpoints.
- A disposable Microsoft 365 tenant with the E5 licensing, Purview configuration,
  and pay-as-you-go billing required by the linked integration guide.
- Tenant administrators for Graph application-role assignments, Purview billing,
  Audit/DSPM for AI onboarding, and DLP policy configuration.

Reuse the original sample's [Work IQ and fictional guidelines setup](../microsoft-enterprise-architect-hosted/README.md#prerequisites-and-connections)
in the **same project**. The Work IQ connection must target
`https://workiq.svc.cloud.microsoft/mcp`, normally named `workshop-workiq-mcp`.
Complete delegated OAuth as the workshop user; Azure CLI login is not Work IQ
consent. Microsoft Learn uses `https://learn.microsoft.com/api/mcp` without a
connection. No toolbox or separate SharePoint MCP connection is used.

## Configure and deploy

Run from this directory:

```bash
cd samples/microsoft-enterprise-architect-purview-hosted
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit endpoint, model, Work IQ connection, and the three Purview UUIDs.
set -a
source .env
set +a
az login
python -m app.deploy
```

Environment files are not loaded automatically; source only trusted shell values.
No secrets or tokens belong in `.env`, source, or the deployment ZIP.

| Variable | Value |
| --- | --- |
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint |
| `FOUNDRY_MODEL_DEPLOYMENT` | Model deployment name |
| `WORKIQ_CONNECTION_NAME` | Existing Work IQ project connection |
| `PURVIEW_TENANT_ID` | Entra tenant UUID |
| `PURVIEW_APP_ID` | Application/client UUID used as the Purview policy location |
| `PURVIEW_USER_ID` | Licensed workshop user's directory **object UUID**, not UPN or agent ID |

Register a dedicated single-tenant app in Entra ID, or use an administrator-approved
existing app, for `PURVIEW_APP_ID`. This is an explicit policy location; it need
not be the runtime identity and requires no client secret. All three Purview IDs
are required and validated as UUIDs. There is no DLP opt-out flag.

The deployer resolves and validates the Work IQ connection before uploading only
`main.py`, `requirements.txt`, and the runtime modules `app/__init__.py`,
`agent.py`, `config.py`, `host.py`, and `purview.py`. It passes only non-secret
endpoint/model/connection and Purview IDs to the runtime. Foundry installs
dependencies, creates a hosted Responses **2.0.0** version, and runs
`python main.py`. Traffic switches only after the version becomes active;
existing protocol/publishing settings are preserved. Failed versions remain for
diagnosis and the temporary ZIP is removed.

Deployment **does not** grant Graph permissions, enable billing, or create
Purview policies. Complete the following setup before invoking.

## Set up Microsoft Purview

### 1. Enable the tenant

Follow [Configure Microsoft Purview](https://learn.microsoft.com/purview/developer/configurepurview)
to onboard DSPM for AI, activate Audit, enable pay-as-you-go billing, and configure
the enterprise-app collection policy. Collection can retain prompts/responses;
agree ingestion and retention with the tenant administrator. Use synthetic data.

### 2. Grant the runtime Graph application permissions

The unattended host uses its existing `DefaultAzureCredential` for Foundry and
Purview. It does not open a browser, accept caller-supplied bearer tokens, or
copy participant credentials. Find the **actual hosted runtime managed/agent
identity service principal** in Foundry/Entra and confirm which principal the
credential uses.

Have a tenant administrator grant these Microsoft Graph **application** roles
to that runtime principal:

| Role | SDK operation |
| --- | --- |
| `Content.Process.All` | Inline content evaluation |
| `ProtectionScopes.Compute.All` | Protection scope computation |
| `ContentActivity.Write` | Content activity reporting |

Foundry Azure RBAC is not Graph consent. Assigning roles only to the deployer's
login, a blueprint app, or the policy-location app does not authorize the runtime.
For managed identities/service principals, an administrator with authority to
assign Microsoft Graph app roles can use:

```bash
RUNTIME_SP_OBJECT_ID="<actual-runtime-service-principal-object-uuid>"
GRAPH_APP_ID="00000003-0000-0000-c000-000000000000"
GRAPH_SP_ID=$(az ad sp show --id "$GRAPH_APP_ID" --query id -o tsv) || exit 1
# Review existing assignments first; don't recreate roles already assigned.
az rest --method GET \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$RUNTIME_SP_OBJECT_ID/appRoleAssignments"
for ROLE in Content.Process.All ProtectionScopes.Compute.All ContentActivity.Write; do
  ROLE_ID=$(az ad sp show --id "$GRAPH_APP_ID" \
    --query "appRoles[?value=='$ROLE' && contains(allowedMemberTypes, 'Application')].id | [0]" -o tsv) || exit 1
  test -n "$ROLE_ID" && test "$ROLE_ID" != "None" || { echo "Missing Graph role: $ROLE" >&2; exit 1; }
  az rest --method POST \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$RUNTIME_SP_OBJECT_ID/appRoleAssignments" \
    --body "{\"principalId\":\"$RUNTIME_SP_OBJECT_ID\",\"resourceId\":\"$GRAPH_SP_ID\",\"appRoleId\":\"$ROLE_ID\"}" || exit 1
done
az rest --method GET \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$RUNTIME_SP_OBJECT_ID/appRoleAssignments"
```

Verify all three assignments and tenant consent. If the platform identity cannot
obtain Graph tokens with these roles, stop: the runtime is not ready for this
app-only SDK path. Do not substitute blueprint secrets or broadly privileged
credentials. This sample does not provision Agent 365 identities or change their
authentication flows.

### 3. Create or reuse an enforcing DLP policy

In Purview DLP, use a dedicated **Applications / Managed cloud apps** location
scoped to `PURVIEW_APP_ID`, enforcement plane **Application**, the workshop user,
and a sensitive information type such as **Credit Card Number**. Enable an
**UploadText** prompt rule with **Restrict access / Block**, in enforced mode,
not simulation/audit-only. Do not mix this location with Exchange/SharePoint/Teams
locations. An existing policy must cover this exact application and user.

Alternatively, a Purview administrator can create a dedicated test policy:

```powershell
Install-Module ExchangeOnlineManagement -Scope CurrentUser
Connect-IPPSSession
$appId = '<PURVIEW_APP_ID>'
$locations = '[{"Workload":"Applications","Location":"' + $appId + '","LocationDisplayName":"MEA Purview workshop","LocationSource":"Entra","LocationType":"Individual","Inclusions":[{"Type":"Tenant","Identity":"All"}]}]'
New-DlpCompliancePolicy -Name 'MEA Purview workshop' -Mode Enable `
  -Locations $locations -EnforcementPlanes @('Application')
New-DlpComplianceRule -Name 'MEA block test cards' -Policy 'MEA Purview workshop' `
  -ContentContainsSensitiveInformation @(@{Name='Credit Card Number'}) `
  -RestrictAccess @(@{setting='UploadText';value='Block'})
```

The PowerShell example covers all tenant users at this one application location;
use a dedicated test tenant or have the administrator narrow the user scope.
Review existing policies instead of overwriting them. Allow up to an hour for
propagation. Collection/Audit policies alone do **not** enable prompt blocking.

This workload supports input blocking; response submission provides
evaluation/audit as supported by the configured workload. The SDK replaces a
response only if Purview actually returns a blocking action. Do not assume that
the supplied UploadText policy filters sensitive responses.

### 4. Verify a blocked turn

Restart/redeploy after configuration changes, then invoke:

```bash
python -m app.invoke "Summarize zero trust in one sentence."
python -m app.invoke "My credit card number is 4111 1111 1111 1111."
```

The ordinary prompt must reach the model; the synthetic test card must return
**Prompt blocked by policy** with **no model or MCP call**. Confirm that boundary
in runtime model/tool traces and inspect Purview Activity explorer. A successful
response or empty action list is not proof of enforcement. An error/timeout must
fail the turn, not return an unchecked answer. Reports can lag by a day.

Troubleshooting: 401/403 means check the actual runtime identity, role assignments,
and consent; 402 means check licensing/billing; no block means check the app ID,
user scope, enforced UploadText rule, SIT match, and propagation. Do not enable
`ignore_exceptions` or `ignore_payment_required` as a workaround.

For **local runtime** testing, also set the full `WORKIQ_CONNECTION_ID` and select
an identity with equivalent Graph permissions and Foundry access before running
`python main.py`. An ordinary `az login` for deployment does not grant these
permissions. With a delegated Graph token, the SDK uses the token's authenticated
user in preference to the configured app-only fallback user; keep it the same
workshop user and grant delegated permissions documented by each Graph API.

## Enforcement boundary

[`app/purview.py`](app/purview.py) binds copied messages to the trusted workshop
user, overwriting caller-supplied `user_id` and `author_name`, then attaches
`PurviewChatPolicyMiddleware` **last** on `FoundryChatClient`. The SDK handles
Graph calls, scopes, caching, and actions; there is no custom REST guard.
`PurviewSettings` is a mapping with an explicit application location and both
exception/payment bypasses disabled.

The pinned preview SDK returns a plain `ChatResponse` when it blocks a prompt,
even for streaming calls. `HostedPurviewChatPolicyMiddleware` adapts only that
termination result into a `ResponseStream` containing the same block notice,
as required by the hosted Responses pipeline. Policy decisions remain SDK-owned.

Chat middleware checks the prepared model request (including replayed history
and framework-added context) and the response on every model round trip.
Streaming is buffered for evaluation before release, so token-by-token delivery
is unavailable. SDK authentication, billing, and service errors propagate.

**Foundry executes native MCP tools server-side.** Python middleware cannot
intercept intermediate MCP arguments/results before Foundry executes a tool or
before a public MCP endpoint sees them. Purview is not per-tool authorization,
transaction rollback, or proof that sensitive data never reaches Microsoft
Learn. Keep the synthetic-data restriction, consent, source permissions, and
Work IQ tenant policy. Use client-executed tools with explicit tool-boundary
controls if every outbound tool payload needs inline DLP.

Model-side `store=False` remains enabled, but the outer Responses host and Purview
collection policies may retain conversation data. Response replacement does not
delete provider-managed records. Never share response IDs; `app.invoke --debug`
can expose private tool data.

## Workshop, publishing, and cleanup

Reuse the original sample's [invocation examples](../microsoft-enterprise-architect-hosted/README.md#invoke)
and the prompt sample's [workshop guide](../microsoft-enterprise-architect-prompt/WORKSHOP.md)
with this copy's deployment name. Keep the prompt and original hosted agents
independent. Verify ordinary and blocked prompts on each intended surface.

Do not follow the original sample's organization-wide Teams publishing guidance
for this fixed-user variant. If testing native publishing, use **Just you** for
the same configured workshop user and display name **Microsoft Enterprise
Architect Purview Hosted**. Do not claim multi-user policy attribution is tested.

Remove any personal Teams distribution, then delete only this sample's agent
and all its versions:

```bash
python -m app.delete
# Custom names must match on deploy, invoke, and delete:
python -m app.delete --name custom-mea-purview-hosted
```

Deletion leaves shared project/model resources, Work IQ connections, SharePoint
content, and the original agents untouched. Have the administrator separately
review/remove only workshop-created Purview policies, role assignments, and app
registrations, subject to retention requirements. Hosted compute, model/MCP
calls, and Purview APIs/ingestion can incur charges.

## Offline checks

```bash
pip install -r requirements-dev.txt
pytest
```

Tests cover the copied sample's connection validation, ZIP allowlist, deployment
and routing, cleanup, and Responses/MCP caller-context forwarding. Purview tests
exercise the installed SDK with mocked Graph/model I/O, including input blocking,
error propagation, trusted user binding, and response/stream evaluation.
They do not verify live identity grants, billing, policy propagation, model
safety, MCP access, or Teams publishing. Those require the live checks above.
