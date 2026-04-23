// Function App on Consumption (Y1) + its backing Storage + App Service Plan.
//
// Consumption plan works identically in Azure Global and Azure China; no
// preview features. The App Settings surface is the single place each
// runtime env var is declared, so adding a new one requires exactly one
// edit here and one corresponding parameter (ADR 0003 — cloud-specific
// values come in via main.bicep's parameter file).

param namePrefix string
param location string

@description('User-Assigned Managed Identity resource id to attach to the Function App.')
param managedIdentityId string

@description('App Insights connection string for Function App telemetry.')
param appInsightsConnectionString string

@description('Whether to mount the /api/chat agent route and provision its env vars.')
param enableReferenceAgent bool

@description('All environment variables the runtime reads — cloud-neutral keys, populated per-cloud by the parameter file.')
param runtimeAppSettings object

var storageName = toLower(replace('${namePrefix}sa', '-', ''))

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan'
  location: location
  kind: 'functionapp'
  sku: {
    name: 'Y1'
    tier: 'Dynamic'
  }
  properties: {
    reserved: true  // Linux
  }
}

var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

var baseAppSettings = [
  {
    name: 'AzureWebJobsStorage'
    value: storageConnectionString
  }
  {
    name: 'FUNCTIONS_EXTENSION_VERSION'
    value: '~4'
  }
  {
    name: 'FUNCTIONS_WORKER_RUNTIME'
    value: 'python'
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

// Expand the caller-supplied settings dict into AppSetting records. Each key
// in runtimeAppSettings becomes one app setting — keeps the parameter file
// flat and readable.
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
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: concat(baseAppSettings, runtimeSettingsArray)
    }
  }
}

output functionAppName string = functionApp.name
output functionAppId string = functionApp.id
output defaultHostName string = functionApp.properties.defaultHostName
