# Enterprise architect with OBO and unified Work IQ MCP

This Python Agent Framework sample runs an enterprise architect in an ephemeral Azure
Container Apps sandbox. It uses a dedicated Entra agent identity and blueprint to call
Microsoft Foundry and the **unified Work IQ MCP endpoint** on behalf of the signed-in user.
It is a CLI sample, not a Teams bot or an AI Teammate.

## Flow overview

```text
           Signed-in user (public-client device-code sign-in)
                                  |
            CLI launcher: question + blueprint-audience user token
                                  |
               Ephemeral Azure Container Apps sandbox
                                  |
            Python Agent Framework enterprise architect
                                  |
       +--------------------------+--------------------------+
       |                          |                          |
 FoundryChatClient          Work IQ MCP client       Optional Agent 365
       |                          |                     telemetry
 Child-agent OBO           Child-agent OBO                   |
       |                          |                  App-only S2S token
 Foundry model             Unified Work IQ MCP               |
                                  |                 Agent 365 observability
                    Full live catalog, no allowlist
                                  |
          SharePoint / OneDrive / Mail / Teams / Calendar / People
```

The sandbox managed identity authenticates the blueprint, which supplies the exchange
assertion used by the child agent identity. The child combines that assertion with the
signed-in user's token to obtain separate delegated Foundry and Work IQ tokens.
Telemetry uses the child identity's app-only token, never the user's token.
The answer returns through the CLI; the launcher then deletes the sandbox.

Unlike the hosted sample, the CLI provisions the sandbox and the agent code manages
MCP connections and OBO tokens directly, rather than using Foundry-managed OAuth
connections. The detailed authentication flow is shown below.

## Work IQ approach

The sample follows the current [Work IQ overview](https://learn.microsoft.com/microsoft-365/copilot/extensibility/work-iq/)
and [MCP design](https://learn.microsoft.com/microsoft-365/copilot/extensibility/work-iq/mcp/overview):

- One endpoint: **`https://workiq.svc.cloud.microsoft/mcp`**.
- The full live tool catalog, without a tool-name allowlist or per-call approval prompts.
- Generic entity, binary, action, function, agent-question, retrieval, and schema tools.
  The agent discovers resource paths and schemas at runtime instead of inventing them.
- Microsoft 365 context includes SharePoint/OneDrive, mail, calendar, Teams, and people,
  subject to the live service's capabilities, consent, user access, and tenant policy.
- Delegated **`WorkIQAgent.Ask`**, optionally expanded with **`WorkIQ.Act`**.
  No application-only Work IQ tokens or legacy `Tools.ListInvoke.All` scopes.

This uses the same endpoint and full-catalog approach as
[`microsoft-enterprise-architect-hosted`](../microsoft-enterprise-architect-hosted)
and [`microsoft-enterprise-architect-prompt`](../microsoft-enterprise-architect-prompt).
The deliberate difference is authentication: those samples use Foundry-managed delegated
OAuth connections; **this sample keeps direct child-agent OBO**.
It needs no `WORKIQ_CONNECTION_ID`, Foundry Work IQ connection, or `ToolingManifest.json`.
The older Agent 365 per-workload server catalog is not used.

```text
user -> public-client sign-in -> user access token (audience: blueprint)
launcher -> trusted sandbox: code, configuration, user assertion
sandbox managed identity -> blueprint (fmi_path: child agent) -> exchange assertion
child agent + exchange assertion + user assertion -> delegated Foundry token
child agent + exchange assertion + user assertion -> delegated Work IQ token
    -> https://workiq.svc.cloud.microsoft/mcp -> full live catalog
child agent + exchange assertion -> app-only Agent 365 telemetry token
```

OBO preserves the user's permissions; it does not grant tenant-wide access.
Work IQ is required: authentication, discovery, or connection failures are surfaced,
not silently replaced with a tool-free answer.
This sample implements the MCP integration, not Work IQ's separate A2A, REST chat,
or persistent-workspace integrations.

## Prerequisites

- Python 3.11+, Azure CLI, and the [Agent 365 CLI](../../README.md#agent-365-cli-in-codespaces).
- The [shared infrastructure](../../infra/README.md), deployed with
  `deploySandboxGroup=true`, a Foundry project/model, and a sandbox group.
- Container Apps SandboxGroup Data Owner access for the launching user.
- A managed identity on the sandbox group that can obtain
  `api://AzureADTokenExchange` tokens inside the sandbox.
- A tenant administrator to [enable Work IQ](https://learn.microsoft.com/microsoft-365/copilot/extensibility/work-iq/enable-work-iq),
  grant delegated permissions, and configure policy/billing.
  The Work IQ API overview describes usage-based billing independent of Microsoft 365
  Copilot licensing; verify current availability and billing requirements for your tenant.
- Agent ID Developer (or an applicable higher role) for blueprint creation.

## Run locally with the Microsoft Entra SDK sidecar

As an alternative to the sandbox, `python -m app.run_local` runs the same agent as a
plain local process that simulates any hosting platform. It holds **no** blueprint
credential: every token (delegated Foundry and Work IQ via OBO, app-only telemetry)
is negotiated by the [Microsoft Entra SDK for AgentID sidecar](https://learn.microsoft.com/entra/agent-id/authentication-with-auth-sdk-sidecar),
which owns the blueprint credential and the blueprint -> agent identity exchange.
Foundry remains the LLM backend.

```text
agent process --Bearer user token + AgentIdentity--> sidecar /AuthorizationHeader/Agent
              <-- delegated Foundry / Work IQ token (OBO) --
agent process --AgentIdentity--> sidecar /AuthorizationHeaderUnauthenticated/AgentApp
              <-- app-only Agent 365 telemetry token --
```

Prerequisites are the same registration and delegated authorization as below, except
no sandbox and no managed-identity federation. A managed identity is unavailable
locally, so the sidecar uses a blueprint client secret
(`AGENT_BLUEPRINT_CLIENT_SECRET`); in Azure use `SignedAssertionFromManagedIdentity`.

```bash
set -a; source .env; set +a
python -m app.sign_in                                  # writes the user assertion
docker compose -f docker-compose.sidecar.yml up -d     # sidecar on 127.0.0.1:5000
curl http://localhost:5000/healthz
python -m app.run_local "Summarize architecture guidance in my documents"
docker compose -f docker-compose.sidecar.yml down
```

The sidecar API must stay on loopback or a private network. Two downstream entries are
configured: `Agent` (delegated; scopes overridden per call) and `AgentApp` with
`RequestAppToken=true` (telemetry); without that flag the sidecar attempts a user flow.
The inbound user token must be audience-bound to the blueprint, as in the sandbox flow.

## 1. Prepare a fresh registration

```bash
cd samples/agent365-enterprise-architect-workiq
pip install -r requirements.txt
cp .env.example .env
```

Do not copy neighboring samples' blueprint IDs, identity IDs, generated configuration,
or user tokens. If local registration files belong to another sample, **back them up
outside this directory first**, especially `a365.generated.config.json`. 

Fill in `.env` with the tenant and infrastructure outputs. Export only a trusted file:

```bash
set -a
source .env
set +a
az login --allow-no-subscriptions --tenant "$AZURE_TENANT_ID" --use-device-code
```

Create local `a365.config.json` using your validated Agent 365 CLI application ID:

```json
{
  "tenantId": "<tenant-id>",
  "clientAppId": "<Agent 365 CLI client application ID>",
  "agentIdentityDisplayName": "Contoso enterprise architect WorkIQ",
  "agentDescription": "Sandbox-hosted enterprise architect using delegated OBO and unified Work IQ MCP",
  "aiTeammate": false,
  "useBlueprint": true,
  "authMode": "obo"
}
```

The CLI client is **not** the blueprint or the public client used for user sign-in.
Validate CLI authentication/consent before provisioning:

```bash
a365 setup requirements --category Authentication
a365 setup all --agent-name "Contoso enterprise architect WorkIQ" --authmode obo --dry-run
# Verify that the preview creates NEW resources with the expected name and tenant.
a365 setup all --agent-name "Contoso enterprise architect WorkIQ" --authmode obo
```

Do not use `--agent-registration-only` for this fresh setup. Follow any administrator
handoff printed by the CLI. This registers the agent; it does **not** establish the
unified Work IQ delegated grant described below.

Record the **new** blueprint application/client ID, child agent application/client ID,
and child service-principal object ID in `.env`:

```dotenv
AGENT_BLUEPRINT_APP_ID=<new-blueprint-application-id>
AGENT_IDENTITY_CLIENT_ID=<new-child-agent-application-id>
AGENT_IDENTITY_ID=<new-child-agent-service-principal-object-id>
```

```bash
set -a
source .env
set +a
```

Confirm these IDs in Entra and the CLI setup summary. Do not assume that a field named
`agenticAppId` is the child application's client ID; CLI releases can use it for the
service-principal object ID.

Trust the sandbox managed identity on the **new** blueprint:

```bash
python -m app.manage_identity blueprint add-managed-identity \
  --principal-id <sandbox-managed-identity-object-id>
```

The federated credential uses the managed identity's **object ID**. Runtime
`AGENT_BLUEPRINT_MANAGED_IDENTITY_CLIENT_ID` uses its **client ID** for a user-assigned
identity, or stays empty for the sandbox's system-assigned identity.
No blueprint client secret is uploaded to the sandbox.

The management CLI also supports identity lifecycle operations:

```bash
python -m app.manage_identity --help
python -m app.manage_identity identity list
```

`identity whoami` is an app-only management diagnostic, not an OBO/Work IQ test.

## 2. Configure delegated authorization

1. On the new blueprint, expose an API using application ID URI
   `api://<new-blueprint-id>` and delegated scope `access_agent`.
2. Create a separate, single-tenant public-client app registration. Enable public-client
   device code flows, add the blueprint's delegated `access_agent` permission, and obtain
   the required user/admin consent. Set `AGENT_PUBLIC_CLIENT_ID` to its application ID.
3. Have the administrator enable the Work IQ API/service principal in the tenant.
   Follow [Appendix A](#appendix-a-generate-tenant-specific-authorization-curl-commands)
   to load the new blueprint ID from `.env`, resolve tenant-specific object IDs with
   Azure CLI, and obtain a Graph token. Merge the appendix's `requiredResourceAccess`
   fragment into the blueprint manifest, then run its `curl` commands to grant
   **Work IQ > Delegated permissions > `WorkIQAgent.Ask`** and enable delegated-scope
   inheritance to the child agent. Do not copy object IDs from another tenant or sample.
4. Use the same appendix to declare and admin-consent **Azure Machine Learning
   Services > Delegated permissions > `user_impersonation`** for Foundry and enable
   its inheritance. Separately configure required Foundry project/model data-plane
   access for the signed-in user and runtime identity, for example **Foundry User**
   at the appropriate scope. Azure RBAC does not replace OAuth consent, and sandbox
   access does not imply Foundry access.
5. Verify the intended user's Microsoft 365 source permissions, tenant policy, and billing.

See the [Work IQ permissions reference](https://learn.microsoft.com/microsoft-365/copilot/extensibility/work-iq/permissions),
[interactive-agent setup](https://learn.microsoft.com/entra/agent-id/interactive-agent-authentication-authorization-flow),
and [agent OBO protocol](https://learn.microsoft.com/entra/agent-id/agent-on-behalf-of-oauth-flow).

### Expose `access_agent` in the blueprint manifest

Edit the **blueprint application's Microsoft Entra manifest**, not the child agent
identity, `a365.config.json`, or a local tooling manifest.

1. Open the [Microsoft Entra admin center](https://entra.microsoft.com/).
2. Navigate to **Entra ID > Agents > Agent blueprints** and select the new blueprint.
3. Open **Developer settings > Manifest**. The manifest editor is currently in preview;
   see [Manage agent identity blueprints](https://learn.microsoft.com/entra/agent-id/manage-agent-blueprint#view-the-manifest).
4. Merge the following settings into the existing manifest, then save:

   ```json
   {
     "identifierUris": [
       "api://<blueprint-application-client-id>"
     ],
     "api": {
       "requestedAccessTokenVersion": 2,
       "oauth2PermissionScopes": [
         {
           "id": "<new-random-scope-guid>",
           "value": "access_agent",
           "type": "Admin",
           "isEnabled": true,
           "adminConsentDisplayName": "Access the enterprise architect agent",
           "adminConsentDescription": "Allow this application to invoke the enterprise architect agent on behalf of the signed-in user."
         }
       ]
     }
   }
   ```

Replace `<blueprint-application-client-id>` with the blueprint's **Application/client ID**
(`AGENT_BLUEPRINT_APP_ID`), not its object ID. Generate a separate UUID for
`<new-random-scope-guid>`:

```bash
python -c "import uuid; print(uuid.uuid4())"
```

This UUID identifies the scope; it is not an application ID.
**Do not replace the entire manifest with this fragment.** Preserve existing identifier
URIs, scopes, and unrelated properties. If `api` already exists, merge these settings
inside it rather than adding a second `api` object. If `access_agent` already exists,
retain its existing scope GUID rather than creating a duplicate.

Next, open the **separate public-client app registration** identified by
`AGENT_PUBLIC_CLIENT_ID`:

1. Select **API permissions > Add a permission > My APIs**.
2. Select the blueprint. If it is not listed, search **APIs my organization uses**
   using the blueprint's application ID.
3. Choose **Delegated permissions > access_agent**, then add the permission.
4. Have an administrator **grant admin consent**.
5. Under **Authentication**, ensure **Allow public client flows** is enabled for
   device-code sign-in.

The resulting fully qualified scope is
`api://<blueprint-application-client-id>/access_agent`, exactly what
[app/sign_in.py](app/sign_in.py) requests.

This authorizes **public client -> blueprint** access only. It does not grant the
blueprint or child agent any downstream Work IQ or Foundry permissions.

### Work IQ downstream permissions

The default Work IQ OBO request uses the exact scope:

```text
api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask
```

`WorkIQAgent.Ask` is **not read-only**: its documented authorization includes read/write
access to resources accessible to Work IQ agents, scoped to the signed-in user.
For explicitly expanded data actions, add and admin-consent **`WorkIQ.Act`** on the
same blueprint/child authorization path, then set:

```dotenv
WORKIQ_ENABLE_ACTIONS=true
```

The runtime then requests both scopes in the **same Work IQ OBO exchange**:

```text
api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask api://workiq.svc.cloud.microsoft/WorkIQ.Act
```

The permission is `WorkIQ.Act`, not `WorkIQAgent.Act`. This expansion does not bypass
user permissions or tenant policy. Keep `WORKIQ_ENABLE_ACTIONS=false` unless needed.
Re-run user sign-in and verify consent after permission changes. For genuinely read-only
authorization, the service separately documents `WorkIQ.Read`; the default Ask grant
and prompt guidance do not enforce read-only behavior.

Unlike the other samples' Foundry OAuth connections, this direct OBO path does **not**
request `offline_access` or persist refresh tokens. It does not substitute `.default`,
Graph scopes, or legacy per-server scopes for the documented Work IQ scopes.

## 3. Sign in and run

Export the completed `.env` again and sign in:

```bash
set -a
source .env
set +a
python -m app.sign_in
python -m app.run_agent "Find our architecture standards in SharePoint and summarize the key decisions with links."
python -m app.run_agent "Summarize my upcoming architecture review meetings."
```

Sign-in saves a short-lived access token to `AGENT_USER_ASSERTION_FILE` (example:
`.user-token`) with mode `0600`; it never prints the access token.
This input token must target the **blueprint**, not Graph, Work IQ, Foundry, or the child
agent. Re-run sign-in after expiry or when consent/policy requires interaction.
There is no S2S fallback.

The output includes the child identity, blueprint, SDK user-agent, and answer.
The model discovers the server's live generic tools and schemas, including supported
SharePoint paths. There is no separate SharePoint server, copied tool catalog, or
per-workload manifest to maintain.

The launcher installs [requirements-sandbox.txt](requirements-sandbox.txt) in each
new sandbox, including `mcp` for the protocol client and `tenacity` for Agent Framework's
MCP subscription recovery. Both are optional dependencies of the framework but required
by this sample. Installing them only on the host does not fix a sandbox import error.
If a previous run reported missing `mcp` or `tenacity`, retry with the updated
requirements; the next invocation provisions a fresh sandbox and installs them
automatically.

**Behavior:** explicitly requested, authorized writes/actions can run without per-call
approval prompts, like the other enterprise-architect samples. Prompt instructions
restrict writes to the requested task, but are **not an approval enforcement boundary**.
Use a disposable tenant and synthetic data; configure only the necessary operations using
[Work IQ policy governance](https://learn.microsoft.com/microsoft-365/copilot/extensibility/work-iq/mcp/policy-governance-mcp).
OAuth consent, administrator consent, source permissions, and policy are never disabled.

## Agent 365 Activity telemetry

Optional export remains **app-only S2S**, even though model and Work IQ calls use OBO:

```dotenv
ENABLE_A365_OBSERVABILITY_EXPORTER=true
A365_CAPTURE_CONTENT=false
A365_SPONSOR_ID=<actual-blueprint-sponsor-object-id>
A365_SPONSOR_NAME="<actual sponsor display name>"
A365_SPONSOR_EMAIL=<actual sponsor email>
OTEL_LOG_LEVEL=INFO
A365_OBSERVABILITY_LOG_LEVEL="info|warn|error"
```

The sandbox acquires the Observability API token for the child identity using its
managed-identity/FMI chain, never the user assertion. It preserves S2S endpoint
selection, baggage, invocation/inference scopes, output messages, token counts,
refresh, and shutdown flushing. Real Work IQ executions produce `execute_tool` spans.
Caller attribution uses the user object ID from the successfully exchanged Foundry
token rather than the blueprint sponsor.

Content, including tool arguments/results, is redacted by default. Enable capture only
when prompts, responses, and Microsoft 365 data are approved for telemetry storage.
Identity/caller metadata is still exported. A successful answer or flush does not
prove ingestion: verify exporter diagnostics and Activity for the **new child identity**.

## Trust boundary and implementation

The launcher transfers a sensitive user assertion in `/workspace/request.json`, never
in command-line arguments. It protects the request file, removes it on exit, and
deletes the sandbox on success or failure. `.env` and `.user-token` are ignored by Git.

Run only trusted code in this sandbox. The assertion allows acting as the user within
consented permissions, and the managed identity authenticates the blueprint.
Do not execute user/model-generated code there; isolate blueprints and managed identities
between trust boundaries. This CLI is not a public HTTP API. If adapted into one,
validate incoming token signatures, issuer, audience, tenant, lifetime, and scope with
an approved authentication library. SDK claim decoding here is diagnostic; Entra
validates the assertion during OBO.

`MCPStreamableHTTPTool` discovers the unified server's full live tool catalog.
Each MCP HTTP request obtains the appropriate cached/refreshed delegated Work IQ
token. HTTP redirects are disabled; the endpoint is fixed to the official HTTPS URL.

| File | Purpose |
| --- | --- |
| [app/sign_in.py](app/sign_in.py) | Public-client device sign-in for a blueprint-audience assertion. |
| [app/agent_token.py](app/agent_token.py) | FMI exchange, app-only management/export tokens, and child-identity OBO. |
| [app/workiq.py](app/workiq.py) | Unified Work IQ MCP endpoint, explicit delegated scopes, and live tool discovery. |
| [app/host.py](app/host.py) | Sandbox provisioning, protected request transfer, execution, and cleanup. |
| [app/sandbox_agent.py](app/sandbox_agent.py) | Delegated Foundry credential and tool-enabled enterprise architect. |
| [app/observability.py](app/observability.py) | App-only export, caller attribution, model/tool scopes, and flushing. |
| [app/manage_identity.py](app/manage_identity.py) | Optional Graph identity-management CLI. |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Offline tests exercise the real MCP client against a fake unified server, full dynamic
catalog loading, exact Ask/Act scopes, OBO caching/refresh, and tracing.
Live verification still requires fresh registration, delegated consent, Work IQ tenant
enablement, billing/policy, user sign-in, and Azure setup. Source changes alone do not
prove that your tenant accepts the child-agent OBO grant.

## Appendix A: Generate tenant-specific authorization curl commands

The commands below construct requests using IDs resolved from **your tenant**, rather
than hard-coded blueprint or service-principal object IDs. Run the Bash blocks in the
same terminal, from this sample directory, after creating the new blueprint.

There are three separate authorization settings:

- `requiredResourceAccess` in the blueprint manifest **declares** required permissions.
- `oauth2PermissionGrants` on its service principal **grants consent**.
- `inheritablePermissions` on the blueprint **allows child identities to inherit**
  eligible grants. This collection is configured through Graph, not by adding a
  root-level property to the manifest editor.

Consenting to the public client's `access_agent` permission does not perform any of
these downstream operations. The runtime requests a Foundry token first, before Work IQ,
so missing Foundry consent can cause `AADSTS65001` before any MCP connection.

### A.1. Resolve IDs and retrieve a Graph token

Use an authorized tenant administrator. Graph token acquisition alone does not grant
administrative rights: delegated grant creation requires appropriate Graph authorization
for `DelegatedPermissionGrant.ReadWrite.All` and a supported administrator role.
Inheritance requires appropriate Graph authorization and blueprint-management rights
(Agent ID Administrator, or Agent ID Developer for owned blueprints).
Permissions consented to Graph Explorer do not automatically apply to the Azure CLI
client. If a call returns `403`, check both the caller's role and the CLI client's
effective Graph permissions; do not broaden the sample's runtime permissions.

Load only a trusted `.env`, then authenticate:

```bash
set -a
source .env
set +a

: "${AZURE_TENANT_ID:?Set AZURE_TENANT_ID in .env}"
: "${AGENT_BLUEPRINT_APP_ID:?Set AGENT_BLUEPRINT_APP_ID to the new blueprint client ID}"

az login --tenant "$AZURE_TENANT_ID" --allow-no-subscriptions

GRAPH_TOKEN="$(
  az account get-access-token \
    --tenant "$AZURE_TENANT_ID" \
    --resource-type ms-graph \
    --query accessToken --output tsv
)"
: "${GRAPH_TOKEN:?Graph token acquisition failed}"
```

Do not print or save `GRAPH_TOKEN`, enable shell tracing (`set -x`), or use `curl -v`:
verbose request logs can expose the Authorization header. Retrieve a new token if it
expires during setup.

Resolve the blueprint application object ID, blueprint principal object ID, and the
two resource principal object IDs. The resource **application IDs** below identify the
Microsoft APIs; their service-principal object IDs are tenant-specific.

```bash
BLUEPRINT_OBJECT_ID="$(
  az rest --method GET \
    --url "https://graph.microsoft.com/v1.0/applications(appId='$AGENT_BLUEPRINT_APP_ID')?\$select=id" \
    --query id --output tsv
)"
BLUEPRINT_SP_ID="$(
  az ad sp show --id "$AGENT_BLUEPRINT_APP_ID" --query id --output tsv
)"
FOUNDRY_SP_ID="$(
  az ad sp show --id 18a66f5f-dbdf-4c17-9dd7-1634712a9cbe --query id --output tsv
)"
WORKIQ_SP_ID="$(
  az ad sp show --id fdcc1f02-fc51-4226-8753-f668596af7f7 --query id --output tsv
)"

: "${BLUEPRINT_OBJECT_ID:?Blueprint application lookup failed}"
: "${BLUEPRINT_SP_ID:?Blueprint principal lookup failed}"
: "${FOUNDRY_SP_ID:?Foundry service principal lookup failed}"
: "${WORKIQ_SP_ID:?Enable the Work IQ service principal in this tenant first}"

INHERITANCE_URL="https://graph.microsoft.com/v1.0/applications/microsoft.graph.agentIdentityBlueprint/$BLUEPRINT_OBJECT_ID/inheritablePermissions"
WORKIQ_GRANT_SCOPE="WorkIQAgent.Ask"
```

Stop on lookup errors; do not substitute another sample's IDs. These variables generate
the tenant-specific URLs and JSON bodies when the following `curl` commands run.
In an OAuth grant body, `clientId` means the blueprint **service-principal object ID**,
not `AGENT_BLUEPRINT_APP_ID` and not the public client's application ID.

### A.2. Declare permissions in the blueprint manifest

Open **Entra ID > Agents > Agent blueprints > your blueprint > Developer settings >
Manifest**. Merge these entries into the root-level `requiredResourceAccess` array:

```json
{
  "requiredResourceAccess": [
    {
      "resourceAppId": "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe",
      "resourceAccess": [
        {
          "id": "1a7925b5-f871-417a-9b8b-303f9f29fa10",
          "type": "Scope"
        }
      ]
    },
    {
      "resourceAppId": "fdcc1f02-fc51-4226-8753-f668596af7f7",
      "resourceAccess": [
        {
          "id": "0b1715fd-f4bf-4c63-b16d-5be31f9847c2",
          "type": "Scope"
        }
      ]
    }
  ]
}
```

The first entry is Foundry `user_impersonation`; the second is Work IQ
`WorkIQAgent.Ask`. Preserve existing resource entries, permissions, `identifierUris`,
`api`, and the `access_agent` scope GUID. Do not replace the entire manifest with this
fragment, duplicate resource entries, or create a second `requiredResourceAccess` key.
Save the merged manifest.

If `WORKIQ_ENABLE_ACTIONS=true`, also add scope ID
`54b3dd6b-e697-4e6a-8fcd-96af0b25009d` with `"type": "Scope"` to the Work IQ entry's
`resourceAccess` array, and run this before creating its consent grant:

```bash
WORKIQ_GRANT_SCOPE="WorkIQAgent.Ask WorkIQ.Act"
```

Otherwise keep the default Ask-only grant. Do not add application permissions.

### A.3. Inspect existing grants and inheritance

These are read-only requests. Run them **before** the POST requests and again afterward:

```bash
curl --fail-with-body --silent --show-error \
  "https://graph.microsoft.com/v1.0/servicePrincipals/$BLUEPRINT_SP_ID/oauth2PermissionGrants" \
  --header "Authorization: Bearer $GRAPH_TOKEN"

curl --fail-with-body --silent --show-error \
  "$INHERITANCE_URL" \
  --header "Authorization: Bearer $GRAPH_TOKEN"
```

If the needed entries already exist, skip their POST requests. To extend an existing
grant, use `PATCH /v1.0/oauth2PermissionGrants/<grant-id>` with the merged space-separated
`scope` values, preserving existing approved scopes. Existing inheritance entries
should be updated rather than duplicated. Do not remove unrelated grants or inheritance.

### A.4. Grant downstream administrator consent

The following requests make persistent tenant changes. `AllPrincipals` is tenant-wide
administrator consent, not application-only access: runtime requests still act on behalf
of a user and remain subject to that user's permissions and applicable policy.

Foundry:

```bash
curl --fail-with-body --silent --show-error \
  --request POST \
  'https://graph.microsoft.com/v1.0/oauth2PermissionGrants' \
  --header "Authorization: Bearer $GRAPH_TOKEN" \
  --header 'Content-Type: application/json' \
  --data @- <<JSON
{
  "clientId": "$BLUEPRINT_SP_ID",
  "consentType": "AllPrincipals",
  "resourceId": "$FOUNDRY_SP_ID",
  "scope": "user_impersonation"
}
JSON
```

Work IQ:

```bash
curl --fail-with-body --silent --show-error \
  --request POST \
  'https://graph.microsoft.com/v1.0/oauth2PermissionGrants' \
  --header "Authorization: Bearer $GRAPH_TOKEN" \
  --header 'Content-Type: application/json' \
  --data @- <<JSON
{
  "clientId": "$BLUEPRINT_SP_ID",
  "consentType": "AllPrincipals",
  "resourceId": "$WORKIQ_SP_ID",
  "scope": "$WORKIQ_GRANT_SCOPE"
}
JSON
```

### A.5. Enable delegated-scope inheritance

Foundry:

```bash
curl --fail-with-body --silent --show-error \
  --request POST \
  "$INHERITANCE_URL" \
  --header "Authorization: Bearer $GRAPH_TOKEN" \
  --header 'Content-Type: application/json' \
  --header 'OData-Version: 4.0' \
  --data '{
    "resourceAppId": "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe",
    "inheritableScopes": {
      "@odata.type": "#microsoft.graph.allAllowedScopes",
      "kind": "allAllowed"
    },
    "inheritableRoles": {
      "@odata.type": "#microsoft.graph.noRoles",
      "kind": "none"
    }
  }'
```

Work IQ:

```bash
curl --fail-with-body --silent --show-error \
  --request POST \
  "$INHERITANCE_URL" \
  --header "Authorization: Bearer $GRAPH_TOKEN" \
  --header 'Content-Type: application/json' \
  --header 'OData-Version: 4.0' \
  --data '{
    "resourceAppId": "fdcc1f02-fc51-4226-8753-f668596af7f7",
    "inheritableScopes": {
      "@odata.type": "#microsoft.graph.allAllowedScopes",
      "kind": "allAllowed"
    },
    "inheritableRoles": {
      "@odata.type": "#microsoft.graph.noRoles",
      "kind": "none"
    }
  }'
```

`allAllowed` allows eligible delegated grants for that resource, including future
additions, to be inherited. It does not grant those permissions by itself.
Application-role inheritance remains disabled. Review future scope additions carefully.

### A.6. Verify and retry

Repeat the two GET requests in A.3. Confirm that the blueprint principal has
`user_impersonation` for `$FOUNDRY_SP_ID` and `WorkIQAgent.Ask` (plus `WorkIQ.Act` only
if enabled) for `$WORKIQ_SP_ID`. Confirm both resource application IDs appear in
`inheritablePermissions`. Inherited permissions need not appear as direct grants on
the child identity.

After allowing permission changes to propagate, clear the administrative token,
sign in again, and retry:

```bash
unset GRAPH_TOKEN
python -m app.sign_in
python -m app.run_agent "Find our architecture standards in SharePoint and summarize the key decisions with links."
```

An `AADSTS65001` still indicates a consent problem; confirm the failing resource and
the blueprint/child IDs. A Foundry data-plane `403` after token acquisition requires a
separate RBAC/policy check. These Graph grants do not assign Foundry User or enable
Work IQ billing, tenant policy, or Microsoft 365 source access.

References: [Create a delegated permission grant](https://learn.microsoft.com/graph/api/oauth2permissiongrant-post?view=graph-rest-1.0)
and [Configure inheritable permissions](https://learn.microsoft.com/entra/agent-id/configure-inheritable-permissions-blueprints).
