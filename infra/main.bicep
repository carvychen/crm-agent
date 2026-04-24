// CRM Agent Platform — root Bicep orchestrator (Slice 9).
//
// Deploys the MCP server + reference agent as one Function App, with
// identity, storage, and monitoring. Cloud-specific values (authority,
// Dataverse suffix, FIC audience) come from the per-cloud parameter
// file; the Bicep itself contains no Azure Global / Azure China literals
// (ADR 0003 applies to IaC as well as source).
//
// The preview Functions MCP extension is NOT used — the MCP SDK is self-
// hosted on a standard HTTP trigger so the code path is identical across
// clouds (ADR 0002). All preview features are off-limits (ADR 0003).
//
// Secrets: there are none. Production uses OBO + Workload Identity
// Federation (ADR 0001), so the Function App's Managed Identity + its FIC
// on the AAD app replaces every long-lived credential. Configuration-only
// values live directly as app settings (no Key Vault needed for the walking
// skeleton).

targetScope = 'resourceGroup'

@description('Short prefix applied to every resource name; keep under 12 chars.')
@minLength(3)
@maxLength(12)
param namePrefix string = 'crmagent'

@description('Azure region. Must be a region available in the target cloud.')
param location string = resourceGroup().location

@allowed([
  'global'
  'china'
])
@description('CLOUD_ENV value — selects authority / FIC audience at runtime; Bicep passes it through as-is to the Function App.')
param cloudEnv string

@description('Customer-specific Dataverse environment URL. Global example: https://<org>.crm.dynamics.com  China example: https://<org>.crm.dynamics.cn')
param dataverseUrl string

@description('AAD application registration client ID that the Managed Identity federates into for OBO.')
param aadAppClientId string

@description('Tenant ID of the AAD application above.')
param aadAppTenantId string

@description('Mount POST /api/chat alongside the MCP server. Set false for MCP-only deployments.')
param enableReferenceAgent bool = true

@description('LLM provider — used only when enableReferenceAgent=true. Slice 2 ships `foundry`; Slice 6 adds others.')
@allowed([
  'foundry'
])
param llmProvider string = 'foundry'

@description('Foundry project endpoint URL. Required when llmProvider=foundry. Blank string otherwise.')
param foundryProjectEndpoint string = ''

@description('Foundry model deployment name.')
param foundryModel string = 'gpt-4o-mini'

@description('Optional Azure Monitor action group for alerts. Leave blank for alerts-without-actions (still visible in Monitor).')
param actionGroupId string = ''

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    namePrefix: namePrefix
    location: location
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    namePrefix: namePrefix
    location: location
  }
}

// Compute the Function App hostname so agent→MCP self-calls have a stable
// target (ADR 0004). The name must match function-app.bicep's resource name.
var functionAppName = '${namePrefix}-fn'
var functionAppHost = '${functionAppName}.azurewebsites.net'

// Cloud-neutral runtime app settings. Nothing here embeds an Azure Global
// or Azure China hostname — authority / FIC audience / Dataverse suffix
// all live in src/config.py keyed off CLOUD_ENV (ADR 0003).
var agentAppSettings = enableReferenceAgent ? {
  LLM_PROVIDER: llmProvider
  FOUNDRY_PROJECT_ENDPOINT: foundryProjectEndpoint
  FOUNDRY_MODEL: foundryModel
  MCP_SERVER_URL: 'https://${functionAppHost}/mcp'
} : {}

var runtimeAppSettings = union({
  CLOUD_ENV: cloudEnv
  AUTH_MODE: 'obo'
  DATAVERSE_URL: dataverseUrl
  AAD_APP_CLIENT_ID: aadAppClientId
  AAD_APP_TENANT_ID: aadAppTenantId
  MANAGED_IDENTITY_CLIENT_ID: identity.outputs.clientId
}, agentAppSettings)

module functionApp 'modules/function-app.bicep' = {
  name: 'function-app'
  params: {
    namePrefix: namePrefix
    location: location
    managedIdentityId: identity.outputs.managedIdentityId
    managedIdentityPrincipalId: identity.outputs.principalId
    managedIdentityClientId: identity.outputs.clientId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    enableReferenceAgent: enableReferenceAgent
    runtimeAppSettings: runtimeAppSettings
  }
}

module alerts 'modules/alerts.bicep' = {
  name: 'alerts'
  params: {
    namePrefix: namePrefix
    location: location
    appInsightsId: monitoring.outputs.appInsightsId
    enableReferenceAgent: enableReferenceAgent
    actionGroupId: actionGroupId
  }
}

output functionAppName string = functionApp.outputs.functionAppName
output functionAppHostName string = functionApp.outputs.defaultHostName
output managedIdentityPrincipalId string = identity.outputs.principalId
output managedIdentityClientId string = identity.outputs.clientId
output logAnalyticsId string = monitoring.outputs.logAnalyticsId
output storageAccountName string = functionApp.outputs.storageAccountName
output deploymentContainerName string = functionApp.outputs.deploymentContainerName
