// Provisions a Microsoft Foundry (Cognitive Services `AIServices`) account using the
// Azure Verified Module for Cognitive Services accounts, a GPT-5 model deployment, and
// an AI Foundry project on top of it, ready to be used with Agent Framework.
targetScope = 'resourceGroup'

@description('Required. Azure region for the resources.')
param location string

@description('Required. Name of the Cognitive Services / Microsoft Foundry account.')
param foundryAccountName string

@description('Required. Name of the AI Foundry project created on the account.')
param foundryProjectName string

@description('Required. Name of the GPT-5 model deployment.')
param gpt5DeploymentName string

@description('Required. Model version for the GPT-5 deployment.')
param gpt5ModelVersion string

@description('Required. Deployment SKU name for the GPT-5 model.')
param gpt5SkuName string

@description('Required. Tokens-per-minute capacity (in units of 1,000) for the GPT-5 deployment.')
param gpt5SkuCapacity int

@description('Optional. Tags applied to the resources.')
param tags object = {}

// Azure Verified Module: https://github.com/Azure/bicep-registry-modules/tree/main/avm/res/cognitive-services/account
module account 'br/public:avm/res/cognitive-services/account:0.19.0' = {
  name: 'foundry-account-deployment'
  params: {
    name: foundryAccountName
    location: location
    kind: 'AIServices'
    sku: 'S0'
    customSubDomainName: foundryAccountName
    // Required so an AI Foundry project can be created on top of this account.
    allowProjectManagement: true
    // Agent Framework authenticates with Microsoft Entra ID credentials (e.g. AzureCliCredential).
    disableLocalAuth: false
    publicNetworkAccess: 'Enabled'
    deployments: [
      {
        name: gpt5DeploymentName
        model: {
          format: 'OpenAI'
          name: 'gpt-5'
          version: gpt5ModelVersion
        }
        sku: {
          name: gpt5SkuName
          capacity: gpt5SkuCapacity
        }
      }
    ]
    tags: tags
  }
}

// The AVM module does not (yet) expose AI Foundry `projects` as a first-class parameter,
// so the project is declared directly against the account it just created.
resource existingAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
  dependsOn: [
    account
  ]
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: existingAccount
  name: foundryProjectName
  location: location
  tags: tags
  properties: {
    displayName: foundryProjectName
    description: 'Project used by the Agent Framework sequential workflow sample.'
  }
}

var normalizedAccountEndpoint = endsWith(account.outputs.endpoint, '/')
  ? account.outputs.endpoint
  : '${account.outputs.endpoint}/'

@description('The resource ID of the Microsoft Foundry account.')
output accountResourceId string = account.outputs.resourceId

@description('The endpoint of the Microsoft Foundry account.')
output accountEndpoint string = account.outputs.endpoint

@description('The Microsoft Foundry project endpoint to use with Agent Framework (FoundryChatClient project_endpoint).')
output projectEndpoint string = '${normalizedAccountEndpoint}api/projects/${project.name}'

@description('The name of the GPT-5 model deployment to use as the Agent Framework `model` parameter.')
output gpt5DeploymentName string = gpt5DeploymentName
