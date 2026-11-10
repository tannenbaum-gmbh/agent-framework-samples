# agent-framework-samples
Yet another agent framework sample repo

Samples for building AI agents with [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python) on Azure / Microsoft Foundry.

## Contents

| Path | Description |
| --- | --- |
| [`.devcontainer/`](.devcontainer) | Codespaces/devcontainer environment with Python, Azure CLI, Bicep, .NET 8 SDK, PowerShell 7+, and the Agent 365 CLI (`a365`), ready to work with Azure and Microsoft Foundry. |
| [`infra/`](infra) | Bicep IaC (using the [Azure Verified Module `avm/res/cognitive-services/account`](https://github.com/Azure/bicep-registry-modules/tree/main/avm/res/cognitive-services/account)) that deploys a Microsoft Foundry account with a GPT-5 model deployment and an AI Foundry project, plus an optional Azure Container Apps sandbox group. |
| [`samples/prompt-agent/`](samples/prompt-agent) | A minimal prompt agent that sends one prompt to a Microsoft Foundry model and prints the response. |
| [`samples/mslearn-prompt-agent/`](samples/mslearn-prompt-agent) | A persistent Foundry prompt agent with Microsoft Learn MCP tools, designed for comparison with the hosted-agent sample. |
| [`samples/enterprise-architect-prompt-agent/`](samples/enterprise-architect-prompt-agent) | An enterprise architect prompt agent using A2A to consult the Microsoft Learn agent and Foundry file search for internal architecture guidelines. |
| [`samples/microsoft-enterprise-architect-prompt/`](samples/microsoft-enterprise-architect-prompt) | Microsoft Enterprise Architect Prompt: a separate prompt agent using Microsoft Learn MCP and the full Work IQ MCP catalog, including SharePoint, without tool-use approval prompts, plus safety exercises and native Teams publishing. |
| [`samples/microsoft-enterprise-architect-hosted/`](samples/microsoft-enterprise-architect-hosted) | A code-owned Foundry hosted enterprise architect with ZIP deployment, Microsoft Learn MCP and the full Work IQ MCP catalog (including SharePoint), no tool-use approval prompts or toolbox, and Teams publishing guidance. |
| [`samples/microsoft-enterprise-architect-sandbox/`](samples/microsoft-enterprise-architect-sandbox) | The enterprise architect hosted outside Foundry as an authenticated Responses API service in the existing ACA sandbox group, with direct Microsoft Learn/Work IQ MCP and supervised token refresh. |
| [`samples/ai-search-rag/`](samples/ai-search-rag) | Classic AI Search RAG: upload the shared architecture guidelines to existing Blob Storage, configure and run an integrated-vectorization indexer, then retrieve chunks and generate grounded answers. |
| [`samples/foundry-iq/`](samples/foundry-iq) | Foundry IQ: reuse the classic Search index and combine internal guidelines, live Microsoft Learn MCP, and web knowledge sources in one knowledge base consumed by a prompt agent. |
| [`samples/architecture-review-workflow/`](samples/architecture-review-workflow) | An explicit Agent Framework workflow: existing enterprise architect draft, independent Microsoft Learn A2A 1.0 review, and architect final recommendation. |
| [`samples/sequential-workflow-api/`](samples/sequential-workflow-api) | A 2-agent sequential workflow (researcher → writer) built with Agent Framework and exposed as a FastAPI endpoint, demonstrating concurrent execution across multiple threads/users. |
| [`samples/conditional-workflow/`](samples/conditional-workflow) | A multi-step content workflow (planner → writer → reviewer) with a conditional revision branch and deterministic tests. |
| [`samples/sandbox-code-agent-evals/`](samples/sandbox-code-agent-evals) | An agent that writes and runs Python inside an [Azure Container Apps sandbox](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-overview), scored with Microsoft Foundry's evaluation service and a CI quality gate. |
| [`samples/sandbox-hosted-agent/`](samples/sandbox-hosted-agent) | A launcher that executes the Agent Framework process itself inside an ephemeral Azure Container Apps sandbox. |
| [`samples/agent365-identity-agent/`](samples/agent365-identity-agent) | Builds on the sandbox-hosted agent: creates and manages a Microsoft Entra agent identity blueprint and agent identities for [Microsoft Agent 365](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/agent-365-sdk), then runs the sandboxed agent as that identity, with the Agent 365 SDK resolving its identity. |
| [`samples/mslearn-hosted-agent/`](samples/mslearn-hosted-agent) | A Foundry-backed agent with Microsoft Learn MCP tools, using the same agent code as a local CLI, local Responses API, or ZIP-deployed Foundry hosted agent without a container registry. |

## Getting started

1. Open this repository in a [GitHub Codespace](https://docs.github.com/en/codespaces) (or reopen it in VS Code's Dev Containers) to get Python, the Azure CLI, Bicep, PowerShell 7+, and the Agent 365 CLI (`a365`) pre-installed.
2. Deploy the Microsoft Foundry environment described in [`infra/README.md`](infra/README.md).
3. Run a sample:
   - [`samples/prompt-agent/README.md`](samples/prompt-agent/README.md)
   - [`samples/mslearn-prompt-agent/README.md`](samples/mslearn-prompt-agent/README.md)
   - [`samples/enterprise-architect-prompt-agent/README.md`](samples/enterprise-architect-prompt-agent/README.md)
   - [`samples/microsoft-enterprise-architect-prompt/README.md`](samples/microsoft-enterprise-architect-prompt/README.md)
   - [`samples/microsoft-enterprise-architect-hosted/README.md`](samples/microsoft-enterprise-architect-hosted/README.md)
   - [`samples/microsoft-enterprise-architect-sandbox/README.md`](samples/microsoft-enterprise-architect-sandbox/README.md)
   - [`samples/ai-search-rag/README.md`](samples/ai-search-rag/README.md)
   - [`samples/foundry-iq/README.md`](samples/foundry-iq/README.md)
   - [`samples/architecture-review-workflow/README.md`](samples/architecture-review-workflow/README.md)
   - [`samples/sequential-workflow-api/README.md`](samples/sequential-workflow-api/README.md)
   - [`samples/conditional-workflow/README.md`](samples/conditional-workflow/README.md)
   - [`samples/sandbox-code-agent-evals/README.md`](samples/sandbox-code-agent-evals/README.md)
   - [`samples/sandbox-hosted-agent/README.md`](samples/sandbox-hosted-agent/README.md)
   - [`samples/agent365-identity-agent/README.md`](samples/agent365-identity-agent/README.md)
   - [`samples/mslearn-hosted-agent/README.md`](samples/mslearn-hosted-agent/README.md)

### Agent 365 CLI in Codespaces

The devcontainer includes the latest stable [PowerShell 7+](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell)
and the VS Code PowerShell extension. Run `pwsh` to start PowerShell; Bash remains
the default terminal shell.

The devcontainer installs the .NET 8 SDK and installs or updates
[`Microsoft.Agents.A365.DevTools.Cli`](https://learn.microsoft.com/microsoft-agent-365/developer/agent-365-cli)
as a global tool for the default `vscode` user during post-create setup.
Its tool directory (`/home/vscode/.dotnet/tools`) is included in `PATH`.

For an existing Codespace, run **Codespaces: Rebuild Container** from the Command Palette
(or **Dev Containers: Rebuild Container** locally). After setup completes, open a new
terminal and verify:

```bash
pwsh --version
a365 --help
```

To install or update the CLI manually once the .NET SDK is available:

```bash
dotnet tool update --global Microsoft.Agents.A365.DevTools.Cli
export PATH="$PATH:$HOME/.dotnet/tools"
a365 --help
```

Installation does not authenticate or register an agent. Follow the
[Agent 365 registry instructions](samples/agent365-identity-agent/README.md#agent-365-registry)
for the identity sample.
