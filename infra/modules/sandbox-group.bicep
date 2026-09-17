// Provisions an Azure Container Apps **sandbox group** (preview) — the top-level
// management boundary for Container Apps Sandboxes — and optionally grants a principal
// the data-plane access needed to create and run sandboxes inside it.
//
// See https://learn.microsoft.com/en-us/azure/container-apps/sandboxes-overview
targetScope = 'resourceGroup'

@description('Required. Azure region for the sandbox group.')
param location string

@description('Required. Name of the Container Apps sandbox group.')
param sandboxGroupName string

@description('Optional. Object ID of a principal to grant the `Container Apps SandboxGroup Data Owner` role on the sandbox group. Leave empty to skip the role assignment.')
param dataOwnerPrincipalId string = ''

@description('Optional. Type of the principal receiving the data-owner role assignment.')
@allowed([
  'User'
  'Group'
  'ServicePrincipal'
])
param dataOwnerPrincipalType string = 'User'

@description('Optional. Tags applied to the sandbox group.')
param tags object = {}

// Built-in role: Container Apps SandboxGroup Data Owner. Required to create, run, and
// delete sandboxes through the `management.azuredevcompute.io` data plane.
var sandboxGroupDataOwnerRoleId = 'c24cf47c-5077-412d-a19c-45202126392c'

#disable-next-line BCP081
resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-02-01-preview' = {
  name: sandboxGroupName
  location: location
  tags: tags
}

resource dataOwnerAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(dataOwnerPrincipalId)) {
  name: guid(sandboxGroup.id, dataOwnerPrincipalId, sandboxGroupDataOwnerRoleId)
  scope: sandboxGroup
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      sandboxGroupDataOwnerRoleId
    )
    principalId: dataOwnerPrincipalId
    principalType: dataOwnerPrincipalType
  }
}

@description('The resource ID of the sandbox group.')
output sandboxGroupResourceId string = sandboxGroup.id

@description('The name of the sandbox group, used as the `AZURE_SANDBOX_GROUP` environment variable.')
output sandboxGroupName string = sandboxGroup.name

@description('The region of the sandbox group, used as the `AZURE_SANDBOX_REGION` environment variable.')
output sandboxGroupLocation string = location
