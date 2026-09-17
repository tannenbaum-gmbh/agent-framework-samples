# agent-framework-samples
Yet another agent framework sample repo

Samples for building AI agents with [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python) on Azure / Microsoft Foundry.

## Contents

| Path | Description |
| --- | --- |
| [`.devcontainer/`](.devcontainer) | Codespaces/devcontainer environment with Python, Azure CLI, and Bicep, ready to work with Azure and Microsoft Foundry. |
| [`infra/`](infra) | Bicep IaC (using the [Azure Verified Module `avm/res/cognitive-services/account`](https://github.com/Azure/bicep-registry-modules/tree/main/avm/res/cognitive-services/account)) that deploys a Microsoft Foundry account with a GPT-5 model deployment and an AI Foundry project, plus an optional Azure Container Apps sandbox group. |
| [`samples/sequential-workflow-api/`](samples/sequential-workflow-api) | A 2-agent sequential workflow (researcher → writer) built with Agent Framework and exposed as a FastAPI endpoint, demonstrating concurrent execution across multiple threads/users. |
| [`samples/sandbox-code-agent-evals/`](samples/sandbox-code-agent-evals) | An agent that writes and runs Python inside an [Azure Container Apps sandbox](https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-overview), scored with Microsoft Foundry's evaluation service and a CI quality gate. |

## Getting started

1. Open this repository in a [GitHub Codespace](https://docs.github.com/en/codespaces) (or reopen it in VS Code's Dev Containers) to get Python, the Azure CLI, and Bicep pre-installed.
2. Deploy the Microsoft Foundry environment described in [`infra/README.md`](infra/README.md).
3. Run a sample:
   - [`samples/sequential-workflow-api/README.md`](samples/sequential-workflow-api/README.md)
   - [`samples/sandbox-code-agent-evals/README.md`](samples/sandbox-code-agent-evals/README.md)
