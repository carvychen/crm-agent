// Default alerts for the MCP server + reference agent Function App.
//
// The four rules below are the minimum viable "someone is paged if this
// breaks" coverage — US 20 requires the system to ship with monitoring on
// day one, and US 29 requires alerts fire to a configured action group.
//
// All four rules use App Insights scheduled-query rules rather than
// Function App platform metrics. The legacy Microsoft.Web/sites metrics
// (Http5xx, HttpResponseTime) exist on Y1/App Service Plan but NOT on
// Flex Consumption — Slice 12's rehearsal caught the hosting-model gap.
// Log-query alerts are portable across hosting models AND across clouds
// (Azure Global ↔ 21Vianet) once Application Insights is workspace-based
// (see modules/monitoring.bicep — classic mode is off by design).

param namePrefix string
param location string

@description('Resource ID of the Application Insights component.')
param appInsightsId string

@description('Whether the reference agent is deployed; gates the /api/chat-specific alert.')
param enableReferenceAgent bool

@description('Optional action group ID; alerts fire without actions if blank, still visible in Monitor.')
param actionGroupId string = ''

// --- 1. HTTP 5xx count --------------------------------------------------
// Any 5xx over five minutes means the Function App is misbehaving and the
// user is seeing errors. Threshold > 0 is deliberately strict — this is a
// low-volume service (100/day target), so even one 5xx matters.
resource http5xx 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${namePrefix}-alert-http-5xx'
  location: location
  properties: {
    displayName: 'Function App emitted a 5xx response — site is failing.'
    severity: 2
    enabled: true
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    scopes: [ appInsightsId ]
    criteria: {
      allOf: [
        {
          query: 'requests | where timestamp > ago(5m) | where resultCode startswith "5" | summarize count()'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
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

// --- 2. p95 server response time ----------------------------------------
// 10s at p95 means the user has given up. Lower severity than 5xx because
// slowness sometimes resolves on its own under Consumption/Flex warm-up.
// Uses App Insights duration (milliseconds) — 10000 ms = 10 s.
resource latency 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${namePrefix}-alert-p95-latency'
  location: location
  properties: {
    displayName: 'Function App p95 response time over 10s for 5 minutes.'
    severity: 3
    enabled: true
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    scopes: [ appInsightsId ]
    criteria: {
      allOf: [
        {
          query: 'requests | where timestamp > ago(15m) | summarize p95=percentile(duration, 95)'
          metricMeasureColumn: 'p95'
          timeAggregation: 'Average'
          operator: 'GreaterThan'
          threshold: 10000
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
