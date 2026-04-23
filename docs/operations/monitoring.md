# Monitoring

**Audience**: on-call / SRE. Every query below runs against the Application Insights resource deployed by `infra/modules/monitoring.bicep`. The Bicep-deployed **alerts** in `infra/modules/alerts.bicep` already cover the critical cases; these queries are for ad-hoc investigation.

**Workspace-based App Insights** is mandatory (ADR 0002 rejects the deprecated classic mode). Every `requests` / `traces` / `dependencies` query here runs identically on Azure Global and Azure China.

## Deployed alerts (Bicep)

| Rule name | Severity | Trigger |
|---|---|---|
| `crm-agent-alert-http-5xx` | 2 | Any 5xx in 5m |
| `crm-agent-alert-p95-latency` | 3 | p95 > 10s for 5m (over 15m window) |
| `crm-agent-alert-auth-failure` | 2 | > 5 requests with HTTP 401 in 5m |
| `crm-agent-alert-chat-4xx` | 2 | > 3 requests to `/api/chat` with 4xx in 10m (only if `ENABLE_REFERENCE_AGENT=true`) |

All alerts fire to the optional action group passed in as a Bicep parameter. If none is set, alerts stay visible in Monitor but don't page — intentional so the stack comes up without a blocking dependency on ticketing / PagerDuty integration.

## Canonical KQL queries

### User-OID + request-ID correlation (US 30)

Every request the agent makes to MCP carries a correlation chain. This query joins them by `operation_Id`:

```kusto
requests
| where timestamp > ago(1h)
| where url endswith "/api/chat" or url endswith "/mcp/"
| extend user_oid = tostring(customDimensions["user_oid"])
| project timestamp, operation_Id, url, resultCode, user_oid, duration
| order by timestamp desc
```

To follow a specific incident, add `| where operation_Id == "<id from the user's error message>"`.

### Tool-call latency breakdown (agent → MCP → Dataverse)

```kusto
dependencies
| where timestamp > ago(1h)
| where name startswith "POST " and target endswith ".crm.dynamics.com"
    or target endswith ".crm.dynamics.cn"
    or target contains "services.ai.azure"
| summarize p50 = percentile(duration, 50), p95 = percentile(duration, 95),
            p99 = percentile(duration, 99), count()
  by bin(timestamp, 5m), target
| render timechart
```

### Top error sources (last 24h)

```kusto
requests
| where timestamp > ago(24h)
| where success == false
| summarize count() by name, resultCode, bin(timestamp, 1h)
| order by count_ desc
```

### Token-cache hit rate

The MCP server caches per-user Dataverse tokens; low hit rate means the cache TTL is wrong or the caller is using a fresh JWT per call (which undermines Invariant 2's production-grade commitment).

```kusto
traces
| where timestamp > ago(1h)
| where message startswith "auth.token_cache"
| summarize hits = countif(message == "auth.token_cache.hit"),
            misses = countif(message == "auth.token_cache.miss")
            by bin(timestamp, 5m)
| extend hit_rate = todouble(hits) / todouble(hits + misses)
| render timechart
```

### Foundry token usage (cost estimation)

```kusto
traces
| where timestamp > ago(24h)
| where message startswith "usage."
| extend tokens = toint(customDimensions["total_tokens"])
| summarize total_tokens = sum(tokens), calls = count()
  by bin(timestamp, 1h), model = tostring(customDimensions["model"])
| render timechart
```

## Dashboard starter

The Bicep deployment does not include a pre-built workbook — they are customer-environment-specific. Recommended panels:

1. **Request volume** — `requests | summarize count() by bin(timestamp, 5m), name | render timechart`
2. **Failure rate** — `requests | summarize failures = countif(success == false), total = count() by bin(timestamp, 5m) | extend rate = todouble(failures) / todouble(total)`
3. **p95 response time** per route — the dependencies query above, grouped by `name`
4. **Users active today** — `requests | where timestamp > startofday(now()) | distinct tostring(customDimensions["user_oid"])`
5. **Current alert state** — link out to `aka.ms/azuremonitor/alerts` filtered by resource group

## Lenovo landing zone integration

When running inside Lenovo's landing zone, the Log Analytics workspace may be pre-provisioned and the Bicep should be updated to reference it rather than create a new one. See the `monitoring.bicep` module for the attachment point — a parameter can be added to accept an existing workspace resource ID.
