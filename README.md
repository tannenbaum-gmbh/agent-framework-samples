# agent-framework-samples
Yet another agent framework sample repo

Samples for building AI agents with [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python) on Azure / Microsoft Foundry.

## Contents

| Path | Description |
| --- | --- |
| [`.devcontainer/`](.devcontainer) | Codespaces/devcontainer environment with Python, Azure CLI, and Bicep, ready to work with Azure and Microsoft Foundry. |
| [`infra/`](infra) | Bicep IaC (using the [Azure Verified Module `avm/res/cognitive-services/account`](https://github.com/Azure/bicep-registry-modules/tree/main/avm/res/cognitive-services/account)) that deploys a Microsoft Foundry account with a GPT-5 model deployment and an AI Foundry project, plus an optional Azure Container Apps sandbox group. |
| [`samples/prompt-agent/`](samples/prompt-agent) | A minimal prompt agent that sends one prompt to a Microsoft Foundry model and prints the response. |
| [`samples/mslearn-prompt-agent/`](samples/mslearn-prompt-agent) | A persistent Foundry prompt agent with Microsoft Learn MCP tools, designed for comparison with the hosted-agent sample. |
| [`samples/enterprise-architect-prompt-agent/`](samples/enterprise-architect-prompt-agent) | An enterprise architect prompt agent using A2A to consult the Microsoft Learn agent and Foundry file search for internal architecture guidelines. |
| [`samples/architecture-review-workflow/`](samples/architecture-review-workflow) | An explicit Agent Framework workflow: existing enterprise architect draft, independent Microsoft Learn A2A 1.0 review, and architect final recommendation. |
| [`samples/sequential-workflow-api/`](samples/sequential-workflow-api) | A 2-agent sequential workflow (researcher → writer) built with Agent Framework and exposed as a FastAPI endpoint, demonstrating concurrent execution across multiple threads/users. |
| [`samples/conditional-workflow/`](samples/conditional-workflow) | A multi-step content workflow (planner → writer → reviewer) with a conditional revision branch and deterministic tests. |
| [`samples/sandbox-code-agent-evals/`](samples/sandbox-code-agent-evals) | An agent that writes and runs Python inside an [Azure Container Apps sandbox](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-overview), scored with Microsoft Foundry's evaluation service and a CI quality gate. |
| [`samples/sandbox-hosted-agent/`](samples/sandbox-hosted-agent) | A launcher that executes the Agent Framework process itself inside an ephemeral Azure Container Apps sandbox. |
| [`samples/mslearn-hosted-agent/`](samples/mslearn-hosted-agent) | A Foundry-backed agent with Microsoft Learn MCP tools, using the same agent code as a local CLI, local Responses API, or ZIP-deployed Foundry hosted agent without a container registry. |

## Getting started

1. Open this repository in a [GitHub Codespace](https://docs.github.com/en/codespaces) (or reopen it in VS Code's Dev Containers) to get Python, the Azure CLI, and Bicep pre-installed.
2. Deploy the Microsoft Foundry environment described in [`infra/README.md`](infra/README.md).
3. Run a sample:
   - [`samples/prompt-agent/README.md`](samples/prompt-agent/README.md)
   - [`samples/mslearn-prompt-agent/README.md`](samples/mslearn-prompt-agent/README.md)
   - [`samples/enterprise-architect-prompt-agent/README.md`](samples/enterprise-architect-prompt-agent/README.md)
   - [`samples/architecture-review-workflow/README.md`](samples/architecture-review-workflow/README.md)
   - [`samples/sequential-workflow-api/README.md`](samples/sequential-workflow-api/README.md)
   - [`samples/conditional-workflow/README.md`](samples/conditional-workflow/README.md)
   - [`samples/sandbox-code-agent-evals/README.md`](samples/sandbox-code-agent-evals/README.md)
   - [`samples/sandbox-hosted-agent/README.md`](samples/sandbox-hosted-agent/README.md)
   - [`samples/mslearn-hosted-agent/README.md`](samples/mslearn-hosted-agent/README.md)
