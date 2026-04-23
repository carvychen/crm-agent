// Default alerts for the MCP server + reference agent Function App.
//
// The four rules below are the minimum viable "someone is paged if this
// breaks" coverage — US 20 requires the system to ship with monitoring on
// day one, and US 29 requires alerts fire to a configured action group.
//
// Metrics-based alerts are used where they exist (p95 latency, HTTP 5xx
// count, availability); the /api/chat 4xx alert uses a log-query rule
// because we care specifically about the agent route, not the Function
// App as a whole. Log-query alerts work identically in Azure Global and
// Azure China once Application Insights is workspace-based (see
// modules/monitoring.bicep — classic mode is off by design).

param namePrefix string
param location string

@description('Resource ID of the Function App being monitored.')
param functionAppId string

@description('Resource ID of the Application Insights component.')
param appInsightsId string

@description('Whether the reference agent is deployed; gates the /api/chat-specific alert.')
param enableReferenceAgent bool

@description('Optional action group ID; alerts fire without actions if blank, still visible in Monitor.')
param actionGroupId string = ''

var actions = empty(actionGroupId) ? [] : [
  {
    actionGroupId: actionGroupId
  }
]

// --- 1. HTTP 5xx count --------------------------------------------------
// Any 5xx over five minutes means the Function App is misbehaving and the
// user is seeing errors. Threshold > 0 is deliberately strict — this is a
// low-volume service (100/day target), so even one 5xx matters.
resource http5xx 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-alert-http-5xx'
  location: 'global'  // metric alerts are always a global resource, not a regional one
  properties: {
    description: 'Function App emitted a 5xx response — site is failing.'
    severity: 2
    enabled: true
    scopes: [ functionAppId ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'Http5xx'
          metricNamespace: 'Microsoft.Web/sites'
          metricName: 'Http5xx'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: actions
  }
}

// --- 2. p95 server response time ----------------------------------------
// 10s at p95 means the user has given up. Lower-intensity (severity 3) than
// 5xx because slowness sometimes resolves on its own under Consumption warm-up.
resource latency 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${namePrefix}-alert-p95-latency'
  location: 'global'
  properties: {
    description: 'Function App p95 response time over 10s for 5 minutes.'
    severity: 3
    enabled: true
    scopes: [ functionAppId ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'HttpResponseTime'
          metricNamespace: 'Microsoft.Web/sites'
          metricName: 'HttpResponseTime'
          operator: 'GreaterThan'
          threshold: 10
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: actions
  }
}

// --- 3. Auth failure (401 count) ----------------------------------------
// A spike in 401s usually means the AAD app's secret rotated or OBO is
// misconfigured — high-signal, worth waking someone up for. Uses a log
// query against App Insights because the Function App Http401 metric is
// less reliable than an Application Insights requests filter.
resource authFailure 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${namePrefix}-alert-auth-failure'
  location: location
  properties: {
    displayName: 'Auth failures (HTTP 401) spike'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    scopes: [ appInsightsId ]
    criteria: {
      allOf: [
        {
          query: 'requests | where timestamp > ago(5m) | where resultCode == 401 | summarize count()'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 5
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: empty(actionGroupId) ? [] : [ actionGroupId ]
    }
  }
}

// --- 4. /api/chat 4xx spike (agent only) --------------------------------
// Only deploys when the reference agent is deployed. A 4xx stream on
// /api/chat usually means the UI is sending bad payloads or the auth layer
// is rejecting tokens — neither should be visible to end users.
resource chatRouteErrors 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = if (enableReferenceAgent) {
  name: '${namePrefix}-alert-chat-4xx'
  location: location
  properties: {
    displayName: '/api/chat 4xx spike (reference agent)'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT10M'
    scopes: [ appInsightsId ]
    criteria: {
      allOf: [
        {
          query: 'requests | where timestamp > ago(10m) | where name startswith "POST /api/chat" | where resultCode startswith "4" | summarize count()'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 3
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: empty(actionGroupId) ? [] : [ actionGroupId ]
    }
  }
}
