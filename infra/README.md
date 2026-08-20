# Infrastructure as Code — Microsoft Foundry environment

This folder contains Bicep templates to deploy a Microsoft Foundry (Azure AI Foundry)
environment used by the samples in this repository. It provisions:

- A resource group
- A Cognitive Services account (`kind: AIServices`), deployed with the
  [Azure Verified Module `avm/res/cognitive-services/account`](https://github.com/Azure/bicep-registry-modules/tree/main/avm/res/cognitive-services/account)
- A **GPT-5** model deployment on that account
- An **AI Foundry project** on the account, ready to be used with
  [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python)

## Files

| File | Description |
| --- | --- |
| `main.bicep` | Subscription-scoped entry point. Creates the resource group and calls `modules/ai-foundry.bicep`. |
| `main.bicepparam` | Default parameter values. Copy/adjust as needed (e.g. `main.local.bicepparam`, which is git-ignored). |
| `modules/ai-foundry.bicep` | Resource-group-scoped module deploying the Cognitive Services account (via AVM), the GPT-5 deployment, and the AI Foundry project. |

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) with the Bicep tooling
  (already installed in this repo's [devcontainer](../.devcontainer/devcontainer.json))
- An Azure subscription with access/quota for the GPT-5 model in the chosen region
- `az login`

## Deploy

```bash
az account set --subscription <subscription-id>

az deployment sub create \
  --location westeurope \
  --template-file infra/main.bicep \
  --parameters infra/main.bicepparam \
  --parameters environmentName=<your-unique-name>
```

## Outputs

The deployment exposes the values needed by the [`sequential-workflow-api`](../samples/sequential-workflow-api)
sample:

| Output | Used for |
| --- | --- |
| `foundryProjectEndpoint` | `FOUNDRY_PROJECT_ENDPOINT` environment variable (`FoundryChatClient(project_endpoint=...)`) |
| `gpt5DeploymentName` | `FOUNDRY_MODEL_DEPLOYMENT` environment variable (`FoundryChatClient(model=...)`) |

Retrieve them after deployment with:

```bash
az deployment sub show \
  --name main \
  --query properties.outputs
```

## Notes

- The GPT-5 model name/version (`gpt-5`, `2025-08-07`) and the `GlobalStandard` SKU capacity
  are parameterized in `main.bicep`/`main.bicepparam` — adjust them to match the model
  catalog values and quota available in your subscription/region.
- `disableLocalAuth` is set to `false` so both Microsoft Entra ID and API-key based
  authentication work; for production scenarios prefer Entra ID only (`disableLocalAuth: true`)
  and grant the `Cognitive Services User`/`Cognitive Services OpenAI User` role to your
  identities instead.
