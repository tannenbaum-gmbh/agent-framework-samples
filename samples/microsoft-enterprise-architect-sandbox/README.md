# Microsoft Enterprise Architect Sandbox

The code-owned enterprise architect from
[`microsoft-enterprise-architect-hosted`](../microsoft-enterprise-architect-hosted)
running **outside Foundry hosting**, as a **Responses API service inside an Azure
Container Apps sandbox**. Reuses the sandbox group already deployed by
[`infra/`](../../infra); creates only one isolated sandbox and an HTTPS port.
No new resource group, Container App, registry, Foundry agent version, or Teams
publication is created. Foundry still supplies the model.

```text
OpenAI SDK / HTTP client -- HTTPS + service bearer key --> ACA sandbox :8080
                                                         ResponsesHostServer
                                                         Agent Framework
                                                           | Foundry model
                                                           | Microsoft Learn MCP
                                                           | Work IQ MCP
Local supervisor -- sandbox SDK + refreshed access tokens --+
```

This is a **single-user workshop service**, not a multi-user identity gateway.
Every holder of the service key acts as the Work IQ user signed into the
launcher. Unlike the Foundry-hosted sample, both MCP clients execute in the
sandbox; no Foundry platform/caller headers or project OAuth connections are
used. Work IQ uses a separate delegated sign-in. A model token or managed
identity cannot substitute for Microsoft 365 user consent.

## Prerequisites

- Python 3.10+ locally (Linux/macOS); the sandbox defaults to `python-3.14`.
- The existing sandbox group name, region, resource group, and subscription
  from the shared infrastructure. **Container Apps SandboxGroup Data Owner**
  at that group's scope for the launcher identity.
- Existing Foundry project endpoint and MCP/tool-capable model deployment
  (default `gpt-5.3-codex-2`), with model invocation access for your Azure
  credential. `az login` or another `DefaultAzureCredential` source.
- A disposable Microsoft 365 tenant with synthetic documents and Work IQ
  enabled, required licensing/billing, source permissions, and tenant policy.
- A tenant app registration for this sample with **Allow public client flows**
  enabled and administrator-consented **delegated** Work IQ permissions.
  Configure `WorkIQ.Read` and `WorkIQAgent.Ask`; add `WorkIQ.Act` only for
  explicitly authorized mutation exercises. The launcher requests
  `api://workiq.svc.cloud.microsoft/.default`, using the permissions already
  consented for this app. Follow the
  [Work IQ permissions reference](https://learn.microsoft.com/microsoft-365/copilot/extensibility/work-iq/permissions)
  and [tenant enablement guide](https://learn.microsoft.com/microsoft-365/copilot/extensibility/work-iq/enable-work-iq).
  Device-code sign-in must be allowed by your tenant's Conditional Access.
- Sandbox egress for PyPI/package downloads, the Foundry endpoint,
  `learn.microsoft.com`, and `workiq.svc.cloud.microsoft`, including required
  service redirects. Authentication and token refresh run on the launcher.

Reuse the hosted sample's fictional guidelines, controlled SharePoint folder,
and workshop exercises. This sample neither uploads documents nor changes tenant
policy. Both MCP tools expose their live catalogs without an application
allowlist or per-tool approval prompts. Writes remain subject to consent,
source permissions, and tenant policy.

## Start the service

```bash
cd samples/microsoft-enterprise-architect-sandbox
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in non-secret deployment values and your public-client app ID.
set -a
source .env
set +a
az login
export SERVICE_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m app.host
```

Environment files are not loaded automatically; source only trusted files.
Do not commit keys or tokens. Sign in with the device-code instructions as the
intended Microsoft 365 participant. Azure login and Work IQ sign-in are separate.

The supervisor obtains both tokens before allocating compute, uploads a fixed
runtime-file allowlist, installs dependencies, starts the server on
`0.0.0.0:8080`, exposes port 8080, and checks its authenticated `/readiness`
through the actual HTTPS ingress before printing the URL. Readiness verifies
the HTTP service, **not** model access or MCP consent. Run an actual question
to verify those separately.

**Keep the supervisor running** in a terminal or under your own process manager.
It refreshes short-lived access tokens and checks readiness every minute.
Refresh failures fail the supervisor and trigger cleanup; expired credentials
are rejected, not silently reused. The sandbox process rereads tokens on model
and Work IQ requests. Refresh credentials, Azure CLI caches, and client secrets
are never uploaded. Token updates are atomic and sensitive files use mode 0600.
Dependencies are installed each time; use a prepared disk image for repeated use.

## Invoke

In another terminal, activate the same environment and securely provide the
**same** `SERVICE_API_KEY` from the supervising terminal. Then:

```bash
export SERVICE_URL=https://YOUR-SANDBOX-INGRESS-HOST
python -m app.invoke \
  "Use the guidelines at <SharePoint folder link> to design supplier invoice automation. Cite requirement IDs and Microsoft documentation."

curl --fail-with-body "$SERVICE_URL/responses" \
  -H "Authorization: Bearer $SERVICE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"mea-sandbox-agent","input":"Compare Azure Functions and Logic Apps using Microsoft Learn.","stream":false,"store":false}'
```

Use the ingress root as the OpenAI SDK `base_url`, **without `/v1`**. Routes
include `POST /responses` and `GET /readiness`. The framework Responses host
also supports SSE with `"stream": true`. The configured deployment selects the
model; the request's `model` is not a switch to an arbitrary Foundry deployment.
The supplied CLI starts fresh requests with `store:false`, reports non-completed
responses, and does not retry potentially mutating turns automatically.

For private-data retrieval, confirm actual citations and distinct source
permissions in the disposable tenant. Authentication failures, unavailable MCP
tools, and policy denials must be resolved at the source, not bypassed by
switching users. Foundry playground consent does not establish this direct
client's consent.

## Boundaries and cleanup

ACA ingress is configured **anonymous at the platform layer**, then protected
by the application's constant-time bearer-key check on **every HTTP route**,
including readiness and response retrieval. WebSockets are rejected. The URL
alone grants no access. There is no unauthenticated health route. TLS terminates
at ACA ingress. Keep the key private; all its holders share one Microsoft 365
identity and any host-managed response state. Do not expose this as a shared
organizational service or forward arbitrary caller tokens/platform headers.

The agent, tools, tokens, and in-memory response state live in trusted sandbox
compute. Do not run untrusted/model-generated code there. Source permissions
and tenant policy are the enforcement boundaries; confidentiality, injection
resistance, and no-overwrite instructions are **not DLP or atomic create-only
enforcement**. Access logging and automatic tracing are disabled here to avoid
capturing private workshop data; inspect sandbox logs only in a trusted context.

Ctrl+C/SIGTERM gracefully ends supervision and **deletes only this sandbox**,
including its ingress and token files. Startup, refresh, or health failures also
trigger deletion. The group, model, connections, other sandboxes, and Microsoft
365 content are untouched. Hard kills, network failures, or deletion failures
can leave a sandbox behind: use the
[sandbox portal](https://aka.ms/aca/sandboxes/portal), identify the
`sample=microsoft-enterprise-architect-sandbox` label, and delete only your
instance. Do not delete the shared group. A lost supervisor cannot refresh
tokens; the service fails closed when they expire. Running compute incurs cost.

Sandboxes are lifecycle-managed preview compute, **not an always-on production
hosting guarantee**. The launcher uses a one-hour idle-suspend window and its
minute-by-minute file/health operations keep its sandbox active. For durable,
multi-user production hosting, use a regular Container App, per-caller
authorization/OBO, durable state, managed credentials, and operational controls.
Native Foundry/Teams publishing from the baseline is not part of this sample.

## Offline checks

```bash
pip install -r requirements-dev.txt
pytest
```

Tests exercise the real Responses HTTP protocol with mocked model transport,
bearer authentication, fresh agent factories, MCP wiring/token refresh, and
sandbox startup, readiness, failure, and cleanup paths. They need no Azure
credentials. Live provisioning, model/MCP access, user consent, and tenant policy
require the Azure/Microsoft 365 checks above and are not proven by offline tests.
