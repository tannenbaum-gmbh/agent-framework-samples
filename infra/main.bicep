// Deploys a Microsoft Foundry (Azure AI Foundry) environment consisting of:
// - A resource group
// - A Cognitive Services (`AIServices` kind) account, deployed via the Azure Verified
//   Module `avm/res/cognitive-services/account`
// - A GPT-5 model deployment on that account
// - An AI Foundry project on the account, ready to be used with Agent Framework
// - Optionally, an Azure Container Apps sandbox group (preview) used by the
//   `sandbox-code-agent-evals` sample to run agent-generated code in isolation
//
// Deploy with (subscription scope):
//   az deployment sub create \
//     --location swedencentral \
//     --template-file infra/main.bicep \
//     --parameters infra/main.bicepparam
targetScope = 'subscription'

@description('Required. Name of the environment used to derive resource names.')
@minLength(3)
@maxLength(16)
param environmentName string

@description('Optional. Azure region for the resources.')
param location string = deployment().location

@description('Optional. Name of the resource group to create.')
param resourceGroupName string = 'rg-${environmentName}'

@description('Optional. Name of the Cognitive Services / Microsoft Foundry account.')
param foundryAccountName string = 'aif-${environmentName}'

@description('Optional. Name of the AI Foundry project created on the account.')
param foundryProjectName string = 'proj-${environmentName}'

@description('Optional. Name of the GPT-5 model deployment.')
param gpt5DeploymentName string = 'gpt-5'

@description('Optional. Model version for the GPT-5 deployment. See the Microsoft Foundry model catalog for the current value.')
param gpt5ModelVersion string = '2025-08-07'

@description('Optional. Deployment SKU name for the GPT-5 model (e.g. GlobalStandard, Standard).')
param gpt5SkuName string = 'GlobalStandard'

@description('Optional. Tokens-per-minute capacity (in units of 1,000) for the GPT-5 deployment.')
param gpt5SkuCapacity int = 10

@description('Optional. Deploy an Azure Container Apps sandbox group (preview) for the `sandbox-code-agent-evals` sample.')
param deploySandboxGroup bool = false

@description('Optional. Name of the Azure Container Apps sandbox group.')
param sandboxGroupName string = 'sbg-${environmentName}'

@description('Optional. Object ID of the principal that runs the sandbox sample (e.g. `az ad signed-in-user show --query id -o tsv`). Leave empty to skip the `Container Apps SandboxGroup Data Owner` role assignment.')
param sandboxDataOwnerPrincipalId string = ''

@description('Optional. Type of the principal receiving the sandbox data-owner role assignment.')
@allowed([
  'User'
  'Group'
  'ServicePrincipal'
])
param sandboxDataOwnerPrincipalType string = 'User'

@description('Optional. Tags applied to all resources.')
param tags object = {
  'azd-env-name': environmentName
}

resource resourceGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module foundry 'modules/ai-foundry.bicep' = {
  name: 'ai-foundry-deployment'
  scope: resourceGroup
  params: {
    location: location
    foundryAccountName: foundryAccountName
    foundryProjectName: foundryProjectName
    gpt5DeploymentName: gpt5DeploymentName
    gpt5ModelVersion: gpt5ModelVersion
    gpt5SkuName: gpt5SkuName
    gpt5SkuCapacity: gpt5SkuCapacity
    tags: tags
  }
}

module sandbox 'modules/sandbox-group.bicep' = if (deploySandboxGroup) {
  name: 'sandbox-group-deployment'
  scope: resourceGroup
  params: {
    location: location
    sandboxGroupName: sandboxGroupName
    dataOwnerPrincipalId: sandboxDataOwnerPrincipalId
    dataOwnerPrincipalType: sandboxDataOwnerPrincipalType
    tags: tags
  }
}

@description('The name of the created resource group.')
output resourceGroupName string = resourceGroup.name

@description('The endpoint of the Microsoft Foundry account.')
output foundryAccountEndpoint string = foundry.outputs.accountEndpoint

@description('The Microsoft Foundry project endpoint to use with Agent Framework (FoundryChatClient project_endpoint).')
output foundryProjectEndpoint string = foundry.outputs.projectEndpoint

@description('The name of the GPT-5 model deployment to use as the Agent Framework `model` parameter.')
output gpt5DeploymentName string = foundry.outputs.gpt5DeploymentName

@description('The name of the Azure Container Apps sandbox group, or an empty string when `deploySandboxGroup` is false.')
output sandboxGroupName string = deploySandboxGroup ? sandbox!.outputs.sandboxGroupName : ''

@description('The region of the Azure Container Apps sandbox group, or an empty string when `deploySandboxGroup` is false.')
output sandboxGroupLocation string = deploySandboxGroup ? sandbox!.outputs.sandboxGroupLocation : ''
