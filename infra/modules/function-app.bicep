// Function App on Flex Consumption (FC1) + its backing Storage + App Service Plan.
//
// Flex Consumption (ADR 0008) is the successor to Linux Consumption (Y1) and
// the hosting model this project targets from Slice 12 onward. Linux Consumption
// reaches EOL 2028-09-30; Flex additionally provides *native* identity-based
// deployment storage via functionAppConfig.deployment.storage, which is exactly
// the zero-secret design ADR 0001 calls for and ADR 0008 records.
//
// Runtime storage (AzureWebJobsStorage) is also identity-based — the Managed
// Identity holds Storage Blob / Queue / Table Data Contributor on the storage
// account, referenced by the runtime via the __blobServiceUri / __queueServiceUri
// / __tableServiceUri / __credential=managedidentity / __clientId quartet.
// `allowSharedKeyAccess: false` is set explicitly so the zero-secret story
// survives on permissive subscriptions where Azure Policy isn't enforcing it.

param namePrefix string
param location string

@description('User-Assigned Managed Identity resource id to attach to the Function App and to use as both AzureWebJobsStorage and functionAppConfig.deployment.storage credential.')
param managedIdentityId string

@description('Principal ID (object ID) of the Managed Identity — used as the roleAssignment principalId on the storage account.')
param managedIdentityPrincipalId string

@description('Client ID of the Managed Identity — published to the Function App as AzureWebJobsStorage__clientId so the runtime picks the right identity.')
param managedIdentityClientId string

@description('App Insights connection string for Function App telemetry.')
param appInsightsConnectionString string

@description('Whether to mount the /api/chat agent route and provision its env vars.')
param enableReferenceAgent bool

@description('All environment variables the runtime reads — cloud-neutral keys, populated per-cloud by the parameter file.')
param runtimeAppSettings object

var storageName = toLower(replace('${namePrefix}sa', '-', ''))
var deploymentContainerName = 'deploymentpackage'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false  // ADR 0008 — enforce identity-based access
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

// Container that Flex Consumption fetches the deployment package from.
// Declared in Bicep so the infrastructure contract is complete — no manual
// post-deploy step to create it, and tear-down removes it atomically.
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: deploymentContainerName
  properties: {
    publicAccess: 'None'
  }
}

// Built-in Azure RBAC role definition IDs (stable across clouds, documented
// at https://learn.microsoft.com/azure/role-based-access-control/built-in-roles).
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'

resource storageBlobDataRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, managedIdentityPrincipalId, storageBlobDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource storageQueueDataRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, managedIdentityPrincipalId, storageQueueDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageQueueDataContributorRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource storageTableDataRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, managedIdentityPrincipalId, storageTableDataContributorRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributorRoleId)
    principalId: managedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Flex Consumption plan (FC1). Flex is Linux-only; the `reserved` flag and
// `linuxFxVersion` that Y1 needed are implicit here.
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan'
  location: location
  kind: 'functionapp'
  sku: {
    tier: 'FlexConsumption'
    name: 'FC1'
  }
  properties: {
    reserved: true
  }
}

// Runtime app settings. Identity-based AzureWebJobsStorage + Flex-implicit
// version/worker (no FUNCTIONS_EXTENSION_VERSION / FUNCTIONS_WORKER_RUNTIME —
// Flex owns those via functionAppConfig.runtime).
var baseAppSettings = [
  {
    name: 'AzureWebJobsStorage__blobServiceUri'
    value: storage.properties.primaryEndpoints.blob
  }
  {
    name: 'AzureWebJobsStorage__queueServiceUri'
    value: storage.properties.primaryEndpoints.queue
  }
  {
    name: 'AzureWebJobsStorage__tableServiceUri'
    value: storage.properties.primaryEndpoints.table
  }
  {
    name: 'AzureWebJobsStorage__credential'
    value: 'managedidentity'
  }
  {
    name: 'AzureWebJobsStorage__clientId'
    value: managedIdentityClientId
  }
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: appInsightsConnectionString
  }
  {
    name: 'AZURE_FUNCTIONS_ENVIRONMENT'
    value: 'Production'  // function_app.py enforces AUTH_MODE=obo under this flag
  }
  {
    name: 'ENABLE_REFERENCE_AGENT'
    value: string(enableReferenceAgent)
  }
]

var runtimeSettingsArray = [for key in items(runtimeAppSettings): {
  name: key.key
  value: string(key.value)
}]

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-fn'
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: managedIdentityId
          }
        }
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
    }
    siteConfig: {
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: concat(baseAppSettings, runtimeSettingsArray)
    }
  }
  dependsOn: [
    // Ensure RBAC grants and the deployment container exist before the
    // Function App initialises — Flex pulls the deployment package on
    // cold-start using the MI, which must already have Data-role access.
    storageBlobDataRoleAssignment
    storageQueueDataRoleAssignment
    storageTableDataRoleAssignment
    deploymentContainer
  ]
}

output functionAppName string = functionApp.name
output functionAppId string = functionApp.id
output defaultHostName string = functionApp.properties.defaultHostName
output deploymentContainerName string = deploymentContainerName
output storageAccountName string = storage.name
