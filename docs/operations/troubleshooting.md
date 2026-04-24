# Troubleshooting

**Audience**: on-call / SRE / platform engineer triaging a reported problem. Every row of the table below names both Azure Global and Azure China failure modes where they differ — per US 22.

**First line of defence**: run [scripts/preflight.py](../deployment/preflight.md) before touching anything. Its structured output covers most of the table below with concrete `remediation:` lines.

## MCP-side problems

| Symptom | Most likely cause | Diagnostic | Remediation |
|---|---|---|---|
| External MCP client cannot list tools | Authorization header missing or invalid | App Insights → requests where `url endswith "/mcp/" and resultCode == 401` | Verify the caller is sending `Authorization: Bearer <token>` with an audience matching `AAD_APP_CLIENT_ID` |
| `list_opportunities` returns empty for everyone | Dataverse app user has no read privilege on opportunities | `dataverse-whoami` in preflight passes, but tool calls return `[]` | Assign a security role on the application user that grants `prvReadOpportunity`; re-check [dataverse-setup.md](../deployment/dataverse-setup.md) |
| `list_opportunities` returns fewer records than expected | Row-level security filtering (expected with OBO) | Compare what the calling user sees in D365 UI directly | Not a bug — this is Invariant 1's intended behaviour. If the user should see more, fix their team / business unit membership in Dataverse, not in the MCP server |
| 5xx on every call | Function App ran out of memory or hit a cold-start retry storm | Function App → Metrics → `MemoryWorkingSet` + `HttpResponseTime` | Upgrade to Premium or Flex Consumption plan if 100/day is being exceeded (Consumption has been the design target; revisit per [ADR 0002](../adr/0002-self-hosted-mcp-sdk.md)) |
| `token-acquisition` fails intermittently | Entra rate limit or MSAL cache corruption | preflight error says `AADSTS50196` (rate limit) | Usually self-heals in 60s; if persistent, restart the Function App to clear the in-memory MSAL cache |
| `token-acquisition` fails with `AADSTS700213` | **FIC audience mismatch for the cloud** | preflight `detail:` line carries the code | Recreate the FIC with the correct audience for `CLOUD_ENV` (see [aad-setup.md](../deployment/aad-setup.md) step 3); Global uses `api://AzureADTokenExchange`, China uses `api://AzureADTokenExchangeChina` |
| DNS resolution fails for `login.microsoftonline.com` (global) or `login.partner.microsoftonline.cn` (china) | Private DNS zone or firewall egress rule missing | preflight `dns-reachability` fails; `nslookup` from inside the VNet confirms | Add the authority host to the Function App's outbound allowlist; in China, confirm the Private DNS zone link targets the 21Vianet fabric, not Global |
| Slow every call, not just first one | Consumption plan is rotating cold starts every few minutes (low usage pattern) | App Insights p95 latency > 1s sustained | Switch to Flex Consumption for a warm always-on worker; budget impact per [bicep-deploy.md](../deployment/bicep-deploy.md) |

## Agent-side (`/api/chat`) problems

Skip this section if `ENABLE_REFERENCE_AGENT=false`.

| Symptom | Most likely cause | Diagnostic | Remediation |
|---|---|---|---|
| `/api/chat` returns 401 | User JWT missing or has wrong audience | App Insights request's `customDimensions` lacks `user_oid` | Verify the UI sends `Authorization: Bearer <user-jwt>` with audience = the AAD app's Application ID URI |
| `/api/chat` returns 404 | `ENABLE_REFERENCE_AGENT=false` (agent route not mounted) | App Settings in Function App | Set `ENABLE_REFERENCE_AGENT=true` and restart; redeploy Bicep if the app setting itself is gone |
| Agent returns "no tools available" | `MCP_SERVER_URL` wrong or self-call fails | App Insights → request chain shows the `/mcp` initialize POST failing | Confirm `MCP_SERVER_URL` is the Function App's own public URL + `/mcp`; Bicep sets it from `defaultHostName` but a manual env-var override would break it |
| Stream stalls mid-response | Foundry deployment overloaded or quota exhausted | App Insights → dependencies → `Http Response Time` on the Foundry call | Preflight `foundry-reachability` catches the common cases; for quota, check Azure AI Foundry portal → Usage |
| LLM repeatedly calls tools without finishing | Compaction strategy evicting key context | App Insights → `customDimensions.compaction_evictions > 0` | Increase `SlidingWindowStrategy(keep_last_groups=...)` in `src/agent/builder.py`; this is a tuning parameter per deployment |
| Agent skips approval for destructive ops | `approval_mode` not wired correctly | `src/agent/builder.py` inspection | Verify `approval_mode={"always_require_approval": ["delete_opportunity"]}`; integration test `test_builder` should catch regressions at PR time |
| `/api/chat` 4xx spike on `/api/chat` | UI is sending malformed payloads | The `crm-agent-alert-chat-4xx` alert fires | Check App Insights → requests → resultCode startswith "4" → customDimensions for the failing request body |

## Deployment-time problems

| Symptom | Most likely cause | Diagnostic | Remediation |
|---|---|---|---|
| `az deployment group create` fails with *"Requested sku 'FC1' is invalid"* or *"location not available"* | Flex Consumption is not GA in the target region — likely on Azure China (21Vianet), where Flex rolled out on a different timeline | `az functionapp list-flexconsumption-locations -o tsv \| grep <region>` returns nothing | Run the pre-deploy region check in [bicep-deploy.md](../deployment/bicep-deploy.md) before `az deployment group create`. If Flex isn't GA for your cloud/region, fall back to Functions Premium (least disruption) per [ADR 0008](../adr/0008-identity-based-storage.md) or wait for GA |
| Every HTTP call returns 503 *"Function host is not running"* immediately after a successful deploy | azure-functions Python SDK's `AsgiFunctionApp` registers `route="/{*route}"` with a leading slash; Flex's ASP.NET Core 8 host rejects the resulting `<prefix>//{*route}` template | App Insights → exceptions → `Microsoft.AspNetCore.Routing.RouteCreationException: An error occurred while creating the route with name 'http_app_func' and template 'api//{*route}'` | Covered by `src/flex_asgi.FlexAsgiFunctionApp` + `host.json` `routePrefix=""`. If the exception reappears after an `azure-functions` bump, run `python -m pytest tests/test_flex_asgi.py -v` — the second test fails loudly when the upstream SDK finally drops the leading slash, and the workaround can then be removed |
| Storage-related errors during deploy: *"Shared key access is not permitted"* | Storage account has `allowSharedKeyAccess: false` (enforced by Bicep and often by Azure Policy) and the deploy transport is trying to use a shared-key connection string | `az deployment group show ... --query properties.error` | This is the design — Flex handles identity-based deployment automatically via `config-zip`. If you see this from a different transport, use `az functionapp deployment source config-zip` (not Kudu `/api/zipdeploy`). See [ADR 0008](../adr/0008-identity-based-storage.md) |
| `admin-consent` fails immediately after `az ad app create` | Entra replication race between Microsoft Graph and AAD Graph | Retrying 10–60 s later succeeds | Use the bounded-retry loop in [aad-setup.md](../deployment/aad-setup.md) step 2 |

## Cross-cloud traps

| Global uses | China uses | Where wrong value surfaces |
|---|---|---|
| `login.microsoftonline.com` | `login.partner.microsoftonline.cn` | preflight `token-acquisition` fail with a connection timeout |
| `api://AzureADTokenExchange` | `api://AzureADTokenExchangeChina` | preflight `token-acquisition` fail with `AADSTS700213` |
| `*.crm.dynamics.com` | `*.crm.dynamics.cn` | preflight `dataverse-whoami` 404 or timeout |
| `*.azurewebsites.net` | `*.chinacloudsites.cn` | `MCP_SERVER_URL` wrong after Bicep output substitution — Bicep reads the Function App's `defaultHostName` so this should be automatic; if wrong, the Function App's outbound-only Private Endpoint is likely misconfigured |

## Where to look next

- `src/preflight/checks.py` — each check's `remediation` string is authoritative
- `docs/operations/monitoring.md` — KQL queries for the requests / dependencies tables
- `docs/operations/secret-rotation.md` — FIC / MI / role-assignment rotation mechanics
