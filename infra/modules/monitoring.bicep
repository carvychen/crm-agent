// Log Analytics workspace + Application Insights.
//
// Application Insights is configured in "workspace-based" mode — the legacy
// classic mode is deprecated in Azure Global and was never GA in Azure
// China. Keeping it workspace-based also lets alert rules in alerts.bicep
// write log-query conditions against the workspace directly.

param namePrefix string
param location string

@description('Retention in days for Log Analytics. Global and China both support ≥ 30 days on PerGB2018.')
@minValue(30)
@maxValue(730)
param retentionDays int = 30

resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${namePrefix}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionDays
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${namePrefix}-appi'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    IngestionMode: 'LogAnalytics'
  }
}

output logAnalyticsId string = logs.id
output appInsightsId string = appInsights.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appInsightsInstrumentationKey string = appInsights.properties.InstrumentationKey
