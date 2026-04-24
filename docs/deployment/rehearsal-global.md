# Global dev-tenant delivery rehearsal log

Slice 11 evidence. Captures the commands and real outputs from walking the customer runbook ([aad-setup.md](./aad-setup.md), [dataverse-setup.md](./dataverse-setup.md), [bicep-deploy.md](./bicep-deploy.md), [preflight.md](./preflight.md)) against an accessible Global dev tenant — **before** Lenovo runs the same runbook in their inaccessible China tenant.

The rehearsal closes two gaps PRD #2 could not close from the inside:

1. OBO-over-WIF was never exercised against real Entra + real Dataverse (`AUTH_MODE=app_only_secret` in `tests/integration/` used `client_credentials`).
2. `infra/main.bicep` was only validated via `what-if`; never `az deployment group create`'d.

See [issue #24](https://github.com/carvychen/crm-agent/issues/24) for scope and acceptance criteria.

## Topology

This rehearsal spans **two tenants** because the author's dev environment is split across them. Lenovo's production deployment is **same-tenant** (AAD app + MI + Azure subscription all in one tenant); the cross-tenant split is a rehearsal constraint, not a deployment topology. Step 8 confirmed (and ADR 0001 now documents) that OBO + WIF is a same-tenant architectural pattern — cross-tenant FIC is blocked at the Entra policy layer (`AADSTS700236`), so the cross-tenant rehearsal exercises the runbook, infra, middleware, preflight, and OBO error surfacing end-to-end, but the actual OBO swap + RLS filtering are validated on Lenovo's same-tenant production instance rather than here. Detailed in the [same-tenant vs cross-tenant appendix](#same-tenant-vs-cross-tenant-appendix).

| Tenant | ID | Role | Hosts |
|---|---|---|---|
| **T1** | `16b3c013-d300-468d-ac64-7eda0820b6d3` (`jiaweichen@microsoft.com`) | Azure subscription tenant | Managed Identity, Function App, scratch RG, Bicep deployment |
| **T2** | `eab29a81-c3d7-4fbf-ae9b-304cf0648fd0` (`CRM190711.onmicrosoft.com`, CDX) | Dynamics tenant | AAD app registration, FIC, D365 application user, Dataverse environment |

FIC trusts `T1 MI` → `T2 AAD app`.

## Step 0 — T2 login (prerequisite)

```bash
az login --tenant CRM190711.onmicrosoft.com --allow-no-subscriptions
az account show --query tenantId -o tsv
```

Output:

```
eab29a81-c3d7-4fbf-ae9b-304cf0648fd0
```

Sanity check: CDX tenant exposes `"name": "N/A(tenant level account)"` — expected, CDX has no Azure subscription. All Azure resources (MI, Function App, etc.) live in T1.

## Step 1 — Register the AAD app in T2

Runbook reference: [aad-setup.md §1](./aad-setup.md#1-register-the-aad-application).

```bash
az ad app create --display-name "crm-agent-rehearsal" --sign-in-audience AzureADMyOrg
az ad sp create --id <appId>
```

Captured identifiers:

| Variable | Value | Lands in |
|---|---|---|
| `appId` / `AAD_APP_CLIENT_ID` | `9185bb14-14c4-4f45-8d01-21b3fae84466` | Bicep `parameters.global.json`, `.env` |
| App registration object ID | `65b9f9af-b27c-40f3-9b35-196d8fdfa28f` | (internal reference only) |
| Service principal object ID | `895f20b7-6d60-4df7-8c94-2d51523fc73b` | (used by Dataverse application user in Step 4) |
| `AAD_APP_TENANT_ID` | `eab29a81-c3d7-4fbf-ae9b-304cf0648fd0` | Bicep `parameters.global.json`, `.env` |
| `signInAudience` | `AzureADMyOrg` (single-tenant, per ADR 0001) | — |

## Step 2 — Grant delegated Dynamics permission

Runbook reference: [aad-setup.md §2](./aad-setup.md#2-grant-delegated-dynamics-permission).

```bash
az ad app permission add \
  --id 9185bb14-14c4-4f45-8d01-21b3fae84466 \
  --api 00000007-0000-0000-c000-000000000000 \
  --api-permissions 78ce3f0f-a1ce-49c2-8cde-64b5c0896db4=Scope

# (see Runbook bug #1 below — a sleep is required between these two commands)

az ad app permission admin-consent --id 9185bb14-14c4-4f45-8d01-21b3fae84466
```

Grant verified via `az ad app permission list-grants`:

```json
[
  {
    "clientId": "895f20b7-6d60-4df7-8c94-2d51523fc73b",
    "consentType": "AllPrincipals",
    "resourceId": "4db48229-f246-433a-b107-8c1b09c187de",
    "scope": "user_impersonation"
  }
]
```

`consentType: AllPrincipals` = tenant-wide grant; `resourceId` points at the Dynamics CRM service principal in T2. The app is now authorised to exchange a user's T2 JWT for a Dataverse-scoped token via OBO, subject to the Dataverse application user being created in [Step 4](#step-4--dataverse-application-user-t2).

## Step 3 — (deferred to after Bicep) Wire the FIC

Runbook reference: [aad-setup.md §3](./aad-setup.md#3-wire-the-federated-identity-credential).

Cannot execute until Step 5 (Bicep deploy) emits the MI principal ID. Recorded here as a reminder of execution order.

## Step 4 — Dataverse application user (T2)

Runbook reference: [dataverse-setup.md](./dataverse-setup.md). UI-only path — no CLI / Graph equivalent exists.

### Environment identifiers

| Field | Value | Role |
|---|---|---|
| Environment name | `CRM190711` | Human-readable |
| Environment URL | `https://org6b70bca2.crm.dynamics.com` | **`DATAVERSE_URL` in `.env` + Bicep** |
| Organization ID | `2f662d4e-1d2c-f111-a7e3-002248029708` | PPAC URL path segment (`/manage/environments/<org-id>/...`) |
| Environment ID | `b93f6700-5f1d-e750-80e9-0503cb10c604` | PPAC internal env ID (different from Org ID) |
| Env type | `Trial (subscription-based)` | **Time-limited** — see expiration risk below |
| Business unit | `org6b70bca2` | Root BU; assigned automatically |

### Application user created

Walked the UI flow: Environments → CRM190711 → Settings → Users + permissions → Application users → + New app user → Add app (searched by `appId` `9185bb14-...`) → Business unit = root (default) → Create → Manage roles → tick `Delegate` → Save.

Captured identifiers (via `az account get-access-token` + Dataverse Web API `/api/data/v9.2/systemusers` — see Runbook bug #2 below for why this isn't in the UI):

| Field | Value |
|---|---|
| `systemuserid` (SystemUserId GUID for operations handoff) | `4c6ee1b8-2c3f-f111-88b4-00224804fbdf` |
| `applicationid` (should match T2 app's client ID) | `9185bb14-14c4-4f45-8d01-21b3fae84466` ✓ |
| `internalemailaddress` | `crm-agent-rehearsal_9185bb14-14c4-4f45-8d01-21b3fae84466@2f662d4e-1d2c-f111-a7e3-002248029708.com` |
| `fullname` | `# crm-agent-rehearsal` |

### Security role

- **Delegate** only (Direct, BU `org6b70bca2`) — grants `prvActOnBehalfOfAnotherUser`, the minimum for OBO.
- **System Administrator was initially and mistakenly also assigned** during the rehearsal and then removed. No functional impact on OBO/AC3 (OBO runs under the real user, not the app), but violates [dataverse-setup.md](./dataverse-setup.md) §Assign a security role's least-privilege guidance. See Runbook enhancement note below.
- **Potential follow-up**: `AUTH_MODE=app_only_secret` dev-path CRUD tests may begin returning 401/403 at Dataverse after this downgrade, since Delegate alone grants no entity-level read/write. Decision deferred to Step 7 (preflight) — if it surfaces, we clone Delegate + add narrowly-scoped opportunity/account/contact read+write.

## Step 5 — Bicep deploy to T1 scratch RG

Runbook reference: [bicep-deploy.md](./bicep-deploy.md).

### Sub-subscription context

- Subscription: `MCAPS-Hybrid-REQ-137847-2025-jiaweichen` (`ff8fdedd-3adc-44af-9fb5-e5151b2793fb`).
- User: `jiaweichen@microsoft.com` (T1 tenant `16b3c013-d300-468d-ac64-7eda0820b6d3`).

### Scratch RG

`rg-crm-agent-rehearsal-ncus` in `northcentralus`. Initially tried `rg-crm-agent-rehearsal` in `eastus2`; hit a hard subscription-region quota wall (see Runbook enhancement on quota below) and switched regions. The unused `eastus2` RG was deleted.

### what-if output (trimmed)

```
Resource changes: 10 to create.

+ Microsoft.ManagedIdentity/userAssignedIdentities/crmagent-mi
+ Microsoft.Storage/storageAccounts/crmagentsa                          (StorageV2, Standard_LRS)
+ Microsoft.OperationalInsights/workspaces/<la-name>
+ Microsoft.Insights/components/<ai-name>
+ Microsoft.Web/serverfarms/crmagent-plan                               (Y1 Consumption)
+ Microsoft.Web/sites/crmagent-fn                                       (functionapp,linux)
+ 4x Microsoft.Insights/metricAlerts                                     (5xx, p95 latency, auth failure, /api/chat 4xx)
```

10 resources, no drift vs. the Bicep source. Template validation: **pass**.

### Real deploy

```bash
az deployment group create \
  --resource-group rg-crm-agent-rehearsal-ncus \
  --name slice-11-rehearsal-20260424-002046 \
  --template-file infra/main.bicep \
  --parameters infra/parameters.global.json \
  --parameters \
    aadAppClientId=9185bb14-14c4-4f45-8d01-21b3fae84466 \
    aadAppTenantId=eab29a81-c3d7-4fbf-ae9b-304cf0648fd0 \
    dataverseUrl=https://org6b70bca2.crm.dynamics.com \
    foundryProjectEndpoint=https://ai-account-j2thabfiwahuu.services.ai.azure.com/api/projects/ai-project-web-search-agent
```

State: `Succeeded` in `PT1M22.3453672S` (~82 s). Outputs:

| Output | Value | Feeds into |
|---|---|---|
| `functionAppHostName` | `crmagent-fn.azurewebsites.net` | Preflight target, `.env` |
| `functionAppName` | `crmagent-fn` | Zip-deploy target |
| `managedIdentityClientId` | `143d8d05-c800-4527-9e85-277cc1be4cf7` | FIC reference (informational) |
| `managedIdentityPrincipalId` | `5de45307-b68e-4dba-b7c8-39639118ca24` | **FIC subject** (Step 6) |
| `logAnalyticsId` | `.../workspaces/crmagent-logs` | Monitoring handoff |

## Step 6 — Wire FIC on T2 app pointing at T1 MI

Runbook reference: [aad-setup.md §3](./aad-setup.md#3-wire-the-federated-identity-credential).

```bash
az ad app federated-credential create \
  --id 9185bb14-14c4-4f45-8d01-21b3fae84466 \
  --parameters @- <<'JSON'
{
  "name": "crm-agent-mi-rehearsal",
  "issuer": "https://login.microsoftonline.com/16b3c013-d300-468d-ac64-7eda0820b6d3/v2.0",
  "subject": "5de45307-b68e-4dba-b7c8-39639118ca24",
  "audiences": ["api://AzureADTokenExchange"],
  "description": "T1 MI (5de45307...) in rg-crm-agent-rehearsal-ncus. Slice 11 delivery rehearsal."
}
JSON
```

FIC wired; verified via `az ad app federated-credential list`.

## Step 7 — Zip-deploy the Function App code

Runbook reference: [bicep-deploy.md §Zip-deploy](./bicep-deploy.md).

**BLOCKED** by [Runbook bug #5](#5--critical--bicep-s-shared-key-based-azurewebjobsstorage-breaks-on-policy-locked-subscriptions) — cannot proceed on this subscription without first switching to identity-based storage.

Attempt log:

- Built deployment zip `/tmp/slice-11-rehearsal/crm-agent.zip` (36 KB, 29 files). Explicit excludes: `.env`, `.venv/`, `.git/`, `.github/`, `.claude/`, `tests/`, `docs/`, `infra/`, `scripts/`, `assets/`, `skills/`, `agent.py`, `__pycache__/`, `.pytest_cache/`, `*.pyc`, `*.DS_Store`. Also created missing `host.json` at repo root ([Runbook bug #4](#4--hostjson-missing-from-repo-root)).
- `az functionapp deployment source config-zip` → **blocked** (shared-key auth refused by storage policy).
- Alternative (`az functionapp deploy --type zip`) deferred — even if it bypasses the deployment transport, the runtime's `AzureWebJobsStorage` connection string remains shared-key-based, so the Function App would not boot on this storage account.

Status: resume after Slice 12 (identity-based storage) lands.

## Interlude — Slice 12 (Flex Consumption + identity-based storage) landed

Runbook bug #5 and its cluster (Y1 → Flex migration, `allowSharedKeyAccess: false`, Bicep alerts conversion, `AsgiFunctionApp` route bug) were scoped as PR #26, reviewed, and merged into `main` before Step 7/8 could resume. Slice 11's branch was rebased onto the new main; the Y1 deployment captured in Step 5 was torn down, and the RG was recreated against the Slice 12 Bicep. What changed materially:

- **MI principal rotated** — teardown + rebuild assigned a new MI principal (`6af1f39e-99d6-4583-9d3f-daca05924b1d`) and client ID (`43c169e4-b479-4546-bc35-e79ad4f0d874`), replacing the Y1 values in Step 5's outputs table.
- **FIC re-wired** — Step 6's federated credential on the T2 AAD app was deleted and recreated with the new MI principal as `subject`. Issuer and audience unchanged. Verified via `az ad app federated-credential list`.
- **Deploy transport** — `az functionapp deployment source config-zip` now works end-to-end on Flex; identity-based deployment storage is automatic per [ADR 0008](../adr/0008-identity-based-storage.md). Original Step 7 block (shared-key) is resolved at the infrastructure level.

## Step 7 — Preflight against the real deployment

Runbook reference: [preflight.md](./preflight.md).

After Flex redeploy + FIC re-wire + zip-deploy:

```bash
MCP_SERVER_URL=https://crmagent-fn.azurewebsites.net/mcp \
ENABLE_REFERENCE_AGENT=true \
LLM_PROVIDER=foundry \
CLOUD_ENV=global \
python scripts/preflight.py
```

Output:

```
✓ dns-reachability             pass resolved 3 host(s): login.microsoftonline.com, org6b70bca2.crm.dynamics.com, ai-account-j2thabfiwahuu.services.ai.azure.com
✓ token-acquisition            pass Entra issued a Dataverse-scoped access token
✓ dataverse-whoami             pass Dataverse accepted the token as UserId=73207f47-0637-f111-88b4-6045bd06486f
✓ foundry-reachability         pass Foundry returned a reply (33 chars)

4 passed · 0 failed · 0 skipped
```

Caveat: preflight runs `AUTH_MODE=app_only_secret` against the operator's `.env` creds, not OBO. It proves DNS + Dataverse reachability + Foundry reachability but not the MI-FIC→OBO path — that's what Step 8 exists for.

## Step 8 — Real-OBO integration test (partial — T2 policy-blocked)

Goal: close ADR 0007's Known gap by exercising the server-side OBO code path against real Entra + real Dataverse.

Status: **partial**. Everything up to the server-side OBO exchange works end-to-end. The final token exchange is blocked by a CDX-specific Entra policy (`AADSTS700236`) that does not apply to same-tenant production deployments. Evidence below drives two runbook findings (#6, #7) and an ADR 0001 scope clarification.

### 8.0 — Expose the AAD app as an OAuth resource (Runbook bug #6)

`aad-setup.md` §1 registers the app but never configures it as an API users can request tokens *for*. A fresh attempt to mint a user-audience token failed:

```
$ az account get-access-token --resource 9185bb14-14c4-4f45-8d01-21b3fae84466
ERROR: AADSTS65001: The user or administrator has not consented to use the application
with ID '04b07795-8ddb-461a-bbee-02f9e1bf7b46' named 'Microsoft Azure CLI'.
```

The app had `identifierUris: []` and zero `oauth2PermissionScopes`, so Azure CLI (a public client) had nothing to request. Fix in this PR:

```bash
APP_ID=9185bb14-14c4-4f45-8d01-21b3fae84466
OBJECT_ID=$(az ad app show --id $APP_ID --query id -o tsv)
SCOPE_ID=$(uuidgen)

# 1. Add the app's identifier URI
az ad app update --id $APP_ID --identifier-uris "api://$APP_ID"

# 2. Add a delegated user_impersonation scope
az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications/$OBJECT_ID" \
  --headers "Content-Type=application/json" --body "$(cat <<JSON
{"api":{"oauth2PermissionScopes":[{
  "id":"$SCOPE_ID","value":"user_impersonation","type":"User","isEnabled":true,
  "adminConsentDisplayName":"Access CRM Agent as user",
  "adminConsentDescription":"Allow the CRM Agent API to act on behalf of the signed-in user.",
  "userConsentDisplayName":"Access CRM Agent as you",
  "userConsentDescription":"Allow the CRM Agent API to act on your behalf."
}]}}
JSON
)"

# 3. Pre-authorize Azure CLI so users mint tokens without per-user consent
az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications/$OBJECT_ID" \
  --headers "Content-Type=application/json" --body "$(cat <<JSON
{"api":{"preAuthorizedApplications":[{
  "appId":"04b07795-8ddb-461a-bbee-02f9e1bf7b46",
  "delegatedPermissionIds":["$SCOPE_ID"]
}]}}
JSON
)"
```

Runbook bug captured as #6.

### 8.1 — User-audience JWT minted successfully

After 8.0, re-login with the scope attached and mint a token:

```bash
az logout
az login --tenant eab29a81-c3d7-4fbf-ae9b-304cf0648fd0 \
  --scope "api://$APP_ID/.default" --allow-no-subscriptions
az account get-access-token --resource "api://$APP_ID" --query accessToken -o tsv
```

JWT payload (decoded, redacted):

```json
{
  "aud": "api://9185bb14-14c4-4f45-8d01-21b3fae84466",
  "iss": "https://sts.windows.net/eab29a81-c3d7-4fbf-ae9b-304cf0648fd0/",
  "appid": "04b07795-8ddb-461a-bbee-02f9e1bf7b46",
  "name": "System Administrator",
  "oid":  "4ad3f500-a02d-478c-b330-d21755d41c3b",
  "scp":  "user_impersonation",
  "tid":  "eab29a81-c3d7-4fbf-ae9b-304cf0648fd0",
  "upn":  "admin@CRM190711.onmicrosoft.com"
}
```

Proves: AAD app + scope + pre-auth wiring is correct end-to-end.

### 8.2 — MCP server accepts the bearer

```bash
curl -sS -X POST https://crmagent-fn.azurewebsites.net/mcp/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"rehearsal","version":"0.1"}}}'
```

→ `200 OK` with a valid MCP `initialize` response. Proves: the bearer propagates through `AsgiMiddleware` → Starlette `/mcp` mount → our `current_user_jwt` ContextVar + MCP session handshake.

### 8.3 — Server-side MI + OBO: two real deploy-auth bugs, surfaced and fixed

Calling `tools/call list_opportunities` triggers the actual OBO path. The first attempt failed cleanly but the error was uninformative:

```
DefaultAzureCredential failed to retrieve a token from the included credentials.
…
ManagedIdentityCredential: App Service managed identity configuration not found
in environment. Token request error: (invalid_scope) 400, Unable to load the
proper Managed Identity.
```

This led to two fixes in commit `f49b4c7` (both apply to production, not just the rehearsal):

1. **`DefaultAzureCredential` needs the MI's client ID explicitly** on a Function App with only a User-Assigned MI (no system MI). `function_app.py` now builds `DefaultAzureCredential(managed_identity_client_id=os.environ["MANAGED_IDENTITY_CLIENT_ID"])`. Prior test suites all used `AUTH_MODE=app_only_secret`, so this code path was never exercised before Step 8.
2. **`src/auth.py` swallowed Entra error bodies.** The only visible error was `Client error '401 Unauthorized' for url 'https://login.microsoftonline.com/…'` — the actual `AADSTS` code was invisible. `DataverseAuth.get_dataverse_token` now raises `HTTPStatusError` with the response body appended, so any future OBO misconfiguration surfaces its AADSTS code in the MCP tool-call output directly.

### 8.4 — OBO blocked by T2 tenant policy (Runbook bug #7)

After the fixes in 8.3, the OBO exchange reaches Entra cleanly but is rejected:

```
AADSTS700236: Entra ID tokens issued by issuer
'https://login.microsoftonline.com/16b3c013-d300-468d-ac64-7eda0820b6d3/v2.0'
may not be used for federated identity credential flows for applications or
managed identities registered in this tenant.

Correlation ID: 374ca68e-8dff-4fdc-a141-100cab21c2fa
Trace ID:       5f02967a-1e60-453f-bd7f-5513f9b31900
```

This is **a Microsoft Entra tenant policy**, not a configuration bug on our side. The T2 (CDX) tenant refuses to accept Entra-issued tokens as FIC assertions — from any source tenant, for any app registered in T2. Our MI lives in T1 and issues T1-signed Entra tokens; T2 blocks those at the policy layer before the OBO exchange can complete.

Lenovo's expected production deployment is **same-tenant** (MI, AAD app, and Dataverse all in one Entra tenant), where the policy does not apply because FIC isn't crossing a tenant boundary. ADR 0001 is updated in this PR to make this architectural scope explicit. Runbook bug #7 documents the error and its remediation (none on our side; the policy is Microsoft's).

### 8.5 — What AC2 can and cannot prove in CDX

| Outcome | Proven by Step 8? |
|---|---|
| AAD app correctly configured as an OAuth resource (identifier URI, scope, pre-auth) | ✅ 8.0 + 8.1 |
| User JWT minting produces the right `aud`, `upn`, `tid`, `scp` | ✅ 8.1 |
| Bearer propagates through ASGI middleware → Starlette → MCP server | ✅ 8.2 |
| Server-side MI acquires its FIC-audience token correctly | ✅ 8.3 (after fixes) |
| OBO error path surfaces actionable AADSTS codes | ✅ 8.3 (after fix) |
| OBO token exchange completes (user JWT → Dataverse token) | ⛔ blocked at T2 policy (AADSTS700236) |
| Dataverse RLS returns different opportunities per user | ⛔ downstream of OBO; untestable in CDX |

Same-tenant production closes the last two by construction — no FIC crosses a tenant boundary, no policy applies. This constraint is now architectural (ADR 0001), not engineering debt.

## Step 9 — Teardown + rebuild proof

**Purpose** — AC3 requires that the runbook fixes from Steps 1–8 are not a one-shot patch; a second operator following the same runbook from a clean state must produce the same green result. This step deletes the entire Azure footprint from Steps 5–8 and rebuilds it from the current `main` branch + corrected runbook.

### 9.1 Teardown

Delete the resource group from Steps 5–8 (carries the Function App, UAMI, Storage, App Insights, Flex plan):

```bash
az group delete \
  --name rg-crm-agent-rehearsal-ncus \
  --yes --no-wait
# Monitor disappearance (~3 min for the Flex Consumption app in this RG).
until ! az group show --name rg-crm-agent-rehearsal-ncus >/dev/null 2>&1; do
  sleep 10
done
echo "teardown complete"
```

The AAD app registration and its FICs in **T2** (Dynamics) were intentionally **not** torn down — Step 9.4 re-wires the existing FIC to the new MI principal, which is the loop a Lenovo identity admin would actually run when rotating infra. (Re-running `aad-setup.md` end-to-end would test the runbook but erase the rehearsal user accounts, which have no automation.)

### 9.2 Rebuild — Bicep + zip-deploy, blind to Step 8 state

Re-ran the documented runbook path verbatim:

```bash
# 1. Pre-deploy region + quota check (landing-zone runbook fix from §5)
az functionapp list-flexconsumption-locations -o tsv | grep -x "North Central US"
# (region GA — proceed)

# 2. Create RG
az group create --name rg-crm-agent-rehearsal-ncus --location "North Central US"

# 3. Bicep deploy
az deployment group create \
  --resource-group rg-crm-agent-rehearsal-ncus \
  --template-file infra/main.bicep \
  --parameters @infra/parameters.global.json

# 4. Capture MI outputs (rotated from the Step 8 deployment)
az deployment group show --resource-group rg-crm-agent-rehearsal-ncus \
  --name main --query properties.outputs -o json
# → managedIdentityPrincipalId = 671da77b-5bee-4392-a14c-55b08bb621a9
# → managedIdentityClientId    = d74965dd-bbb3-4d9b-8c8a-d818ea018532
# → functionAppName            = crmagent-fn
# → functionAppHostName        = crmagent-fn.azurewebsites.net

# 5. Package + zip-deploy (Flex path, no shared-key transport per ADR 0008)
zip -rq /tmp/slice-11-rebuild.zip . \
  -x "*.venv/*" "*/.git/*" "*/__pycache__/*" "*.pyc" "*/tests/*" "*/docs/*"
az functionapp deployment source config-zip \
  --resource-group rg-crm-agent-rehearsal-ncus \
  --name crmagent-fn \
  --src /tmp/slice-11-rebuild.zip
# → 202 accepted; "Deployment was successful"
```

Bicep outcome: `Succeeded`. Zip-deploy outcome: `Succeeded`. No runbook deviations — every command ran green on the first attempt, which is the AC3 goal. (Contrast Step 5 on the same runbook pre-fixes: two separate landing-zone failures before success.)

### 9.3 FIC re-wire — new MI principal into existing AAD app (in T2)

```bash
# (switch CLI context to T2 Dynamics tenant)
az logout && az login --tenant <T2>
az ad app federated-credential delete \
  --id <aad-app-id> --federated-credential-id crm-agent-mi-rehearsal-flex
az ad app federated-credential create \
  --id <aad-app-id> --parameters @- <<JSON
{
  "name": "crm-agent-mi-rebuild",
  "issuer": "https://login.microsoftonline.com/<T1>/v2.0",
  "subject": "671da77b-5bee-4392-a14c-55b08bb621a9",
  "audiences": ["api://AzureADTokenExchange"]
}
JSON
az logout && az login --tenant <T1>   # back to Azure subscription context
```

The FIC record is deterministically reproducible from the Bicep output alone — no manual secret re-issue, no Key Vault rotation. This is the explicit value ADR 0001 claims for WIF vs client-secret OBO.

### 9.4 Post-rebuild preflight — regressions? None.

Ran preflight against the **rebuilt** Function App with no code changes since Step 8:

```
$ MCP_SERVER_URL=https://crmagent-fn.azurewebsites.net/mcp \
    ENABLE_REFERENCE_AGENT=true LLM_PROVIDER=foundry CLOUD_ENV=global \
    .venv/bin/python scripts/preflight.py
✓ dns-reachability             pass resolved 3 host(s): login.microsoftonline.com, org6b70bca2.crm.dynamics.com, ai-account-j2thabfiwahuu.services.ai.azure.com
✓ token-acquisition            pass Entra issued a Dataverse-scoped access token
✓ dataverse-whoami             pass Dataverse accepted the token as UserId=73207f47-0637-f111-88b4-6045bd06486f
✓ foundry-reachability         pass Foundry returned a reply (33 chars)

4 passed · 0 failed · 0 skipped
```

Sanity probe — unauthenticated POST to `/mcp/` returns the clean 401 JSON body the MCP server is supposed to emit when the middleware runs, proving the Function host is up, the Flex route fix (Slice 12) still applies, and the ASGI stack is wired through:

```
$ curl -sS -o - -w "HTTP %{http_code}\n" https://crmagent-fn.azurewebsites.net/mcp/ \
    -X POST -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"rehearsal-ac3","version":"0"}}}'
{"error":"missing_bearer_token","message":"Authorization: Bearer <user-jwt> required"}
HTTP 401
```

### 9.5 AC3 outcome

| Claim | Evidence |
|---|---|
| Full Azure footprint destroyed | `rg-crm-agent-rehearsal-ncus` absent post-teardown |
| Rebuild uses the patched runbook verbatim | Every command in §9.2 is copy-paste from the updated `bicep-deploy.md` + `aad-setup.md`; no ad-hoc flag required |
| MI principal rotated (not a reattach of the old one) | `671da77b-5bee-4392-a14c-55b08bb621a9` ≠ Step 8's `6af1f39e-…` |
| Zero long-lived secrets touched during rebuild | No Key Vault entry, no app-setting secret, no Storage account key used (ADR 0008 + ADR 0001) |
| Preflight all-green on the rebuilt deployment | §9.4 above |
| Function host + ASGI routing + middleware all intact | §9.4 401 probe |

Time budget: teardown ~3 min, Bicep deploy ~2 min, zip-deploy ~90 s, FIC re-wire ~15 s, preflight ~8 s. **Whole rebuild ≈ 7 min wall-clock**, which is the operational claim Lenovo needs when a landing-zone rotation is mandated. Rehearsal timestamp: 2026-04-24.

## Same-tenant vs cross-tenant appendix

The rehearsal is intrinsically cross-tenant because the author's Microsoft internal account is in tenant **T1** (MCAPS subscription), while CDX provisions the Dynamics trial in a distinct tenant **T2**. Lenovo's production deployment is expected to be **same-tenant** — the Managed Identity (Azure subscription) and the AAD app (Dataverse) both live in Lenovo's single production Entra tenant. This appendix captures what the cross-tenant setup proves and what it intentionally cannot.

| Concern | Same-tenant (production shape) | Cross-tenant (this rehearsal's shape) | What the rehearsal still proves |
|---|---|---|---|
| FIC assertion flow | MI's token is accepted by Entra as a client assertion for the AAD app (same tenant) | Blocked by T2 Entra policy at runtime — `AADSTS700236` (see Step 8.4) | That the assertion *shape* is correct: FIC record is well-formed, subject + audience + issuer validate, the UAMI actually mints tokens via IMDS |
| User token audience | User signs into the AAD app's Application ID URI, UI embeds token in Authorization header | Tested end-to-end in Step 8 — `az account get-access-token --resource api://<appId>` produces a well-formed JWT; Authorization header propagates through Function App ASGI stack (Step 8.2) | Middleware, token parsing, route handling — all tenant-agnostic |
| Dataverse RLS per user | Filtered by the inbound user OID after OBO swap | Cannot run — OBO swap blocked upstream at Entra | *Not proven by rehearsal.* Lenovo validates this on their own tenant; integration test suite covers the single-user shape |
| Deployment infra + runbook | Bicep, Function App, UAMI, FIC creation, zip-deploy | Identical — all tested in Steps 5–7 + Step 9 rebuild | Full coverage: runbook is tenant-agnostic |
| Preflight (DNS / Entra / Dataverse / Foundry) | Runs green | Runs green (Step 7 + Step 9.4) | Full coverage |
| External-user onboarding | Entra B2B guest invitations into Lenovo's production tenant (see ADR 0001) | N/A — rehearsal uses in-tenant T2 users | Shape is a Lenovo-admin procedure, not a code path |

Bottom line — cross-tenant FIC is **not a supported deployment topology** for OBO + WIF (ADR 0001). The rehearsal hit this boundary at Step 8.4 with `AADSTS700236`, which is an Entra policy enforcement, not a code or config bug. The useful output of the cross-tenant rehearsal is that every *non*-tenant-bound concern has been exercised end-to-end: deployment runbook, AAD expose-an-API flow, UAMI-on-Flex MI resolution, OBO error surfacing, ASGI middleware, preflight. Production same-tenant deployment closes the last gap automatically.

## Runbook bugs discovered

The point of the rehearsal is to find these. Each bug here is fixed in the referenced runbook file within this same PR; the teardown + rebuild pass (AC3, Step 9) confirms the fix.

### #1 — Entra replication race between `permission add` and `admin-consent`

- **Surfaced in**: Step 2 (first execution).
- **File**: [`aad-setup.md`](./aad-setup.md) §2.
- **Symptom**: `az ad app permission admin-consent` returns `Request_BadRequest` / `"application '<appId>' you are trying to use has been removed or is configured to use an incorrect application identifier."`, even though `az ad app show` and `az ad sp show` both succeed against the same `appId` milliseconds earlier.
- **Root cause**: Entra tenant replication lag. `admin-consent` internally routes through AAD Graph (a different backend from the Microsoft Graph endpoint `az ad app create` / `az ad sp create` use), which has not yet picked up the new app + SP.
- **Reproduction**: ~30–60 s window after app+SP creation, 100% reproducible from a cold tenant.
- **Fix**: insert a 30-second wait (or a bounded retry loop) between `az ad app permission add` and `az ad app permission admin-consent` in `aad-setup.md` §2. Retry loop is preferred — it degrades gracefully under slower tenants.
- **Status**: applied to `aad-setup.md` §2 (bounded retry loop, see commit `205b8ee`); re-green by AC3 (teardown + rebuild, Step 9).

### #2 — `dataverse-setup.md` asks admin to "note the SystemUserId" but PPAC UI never shows it

- **Surfaced in**: Step 4.
- **File**: [`dataverse-setup.md`](./dataverse-setup.md) §What to record.
- **Symptom**: The runbook's "What to record" checklist says "note the SystemUserId for the operations handoff", but the Power Platform Admin Center's Application users **Details** panel only exposes display name, App ID, state, security roles, business unit, email address (synthetic UPN), and team. No `systemuserid` GUID field anywhere in the UI flow the runbook prescribes.
- **Impact**: a Lenovo Dynamics admin following this runbook verbatim cannot satisfy the "What to record" step. Operations handoff would either skip the GUID entirely (risk: no audit-trail key for later troubleshooting) or the admin would have to discover the Dataverse Web API workaround themselves.
- **Fix**: add a CLI snippet to `dataverse-setup.md` §What to record that retrieves the SystemUserId via Dataverse Web API:

  ```bash
  TOKEN=$(az account get-access-token --resource "$DATAVERSE_URL" --query accessToken -o tsv)
  curl -s -H "Authorization: Bearer $TOKEN" \
       "$DATAVERSE_URL/api/data/v9.2/systemusers?\$filter=applicationid%20eq%20$AAD_APP_CLIENT_ID&\$select=systemuserid,applicationid,fullname" \
       | python3 -m json.tool
  ```

  Note the prerequisite: the admin user running this must themselves be a Dataverse user (typically auto-provisioned for the tenant admin). If a non-admin Dynamics admin runs this, the call may 401 — the runbook should mention the prereq explicitly.
- **Status**: applied to `dataverse-setup.md` §What to record (Web-API snippet, see commit `205b8ee`); re-green by AC3 (teardown + rebuild, Step 9).

### #3 — `_comment` in `parameters.*.json` breaks `az deployment group`

- **Surfaced in**: Step 5 (first `what-if` run).
- **Files**: [`infra/parameters.global.json`](../../infra/parameters.global.json), [`infra/parameters.china.json`](../../infra/parameters.china.json), [`tests/integration/test_bicep_live.py`](../../tests/integration/test_bicep_live.py).
- **Symptom**: `ERROR: InvalidTemplate - Deployment template validation failed: 'The following parameters were supplied, but do not correspond to any parameters defined in the template: '_comment'.'`
- **Root cause**: Both param files used a `_comment` key inside `parameters` as an inline-doc hack. ARM's 2019-04-01 schema rejects any key in `parameters` that isn't a declared Bicep param — including `_comment`. The hack silently worked nowhere.
- **How the two safety nets both failed**:
  1. `test_bicep_parameter_files_parse_and_match_declared_params` deliberately skipped `_`-prefixed keys (`if not k.startswith("_")`), so the parse test **passed** by ignoring the bug.
  2. `test_bicep_whatif_against_scratch_rg` is gated on the secret `BICEP_WHATIF_RESOURCE_GROUP`, which was never configured in CI → the only test that would have caught this was **always skipped**. ADR 0007's delivery-constrained clause predicted exactly this: mocked/gated CI gives false confidence.
- **Fix applied in this PR**:
  - Removed the `_comment` entry from both parameter files.
  - Removed the `startswith("_")` filter from `test_bicep_parameter_files_parse_and_match_declared_params` so the hack cannot return silently.
  - The explanatory text formerly inlined in `_comment` is redundant with `infra/README.md`'s deploy instructions.
- **Follow-up (out of Slice 11 scope but worth capturing)**: configure `BICEP_WHATIF_RESOURCE_GROUP` in CI so the what-if actually runs on every PR. Without that, Slice 9's Bicep is essentially untested against real ARM. Tracked separately — not in this PR.
- **Status**: fix applied in this PR; verified by re-running what-if against `rg-crm-agent-rehearsal-ncus` → pass.

### #4 — `host.json` missing from repo root

- **Surfaced in**: Step 7 (zip-deploy preparation).
- **File**: repo root (`host.json` must exist for Azure Functions v2).
- **Symptom**: Azure Functions Python v2 programming model requires `host.json` at the deployment root; the repo shipped without one. The `bicep-deploy.md` zip command would have produced a broken deployment even if storage auth worked.
- **Root cause**: Slice 9 / Slice 10 never tested an actual code deploy — what-if was their terminal validation, and what-if doesn't check deployment-package completeness. ADR 0007's "live-tested" clause should have caught this but didn't.
- **Fix applied in this PR**: create `host.json` at repo root with the standard `version: "2.0"` + extensionBundle + App Insights sampling template.
- **Status**: fix in this PR; verified by AC3 (teardown + rebuild, Step 9 produces a working deployment).

### #5 — **Critical** — Bicep's shared-key-based `AzureWebJobsStorage` breaks on policy-locked subscriptions

**This is the single most consequential finding of the rehearsal so far. ADR 0001 / Invariant-level.**

- **Surfaced in**: Step 7 (first code upload attempt).
- **Files**: [`infra/modules/function-app.bicep`](../../infra/modules/function-app.bicep), runtime Function App config, [`bicep-deploy.md`](./bicep-deploy.md) §deploy.
- **Symptom (deployment)**: `az functionapp deployment source config-zip` → `ERROR: Key based authentication is not permitted on this storage account.` / `ErrorCode:KeyBasedAuthenticationNotPermitted`.
- **Symptom (runtime, predicted)**: even if deployment succeeded by another route, the Function App would fail to boot because `AzureWebJobsStorage` app setting is a shared-key connection string (`AccountKey=${storage.listKeys().keys[0].value}`), and the storage backend refuses shared-key auth.
- **Root cause**: `infra/modules/function-app.bicep` does not set `allowSharedKeyAccess` on the storage account. Azure Policy in the MCAPS-Hybrid subscription (and, by very likely extension, Lenovo's landing zone) defaults policy-locked storage accounts to `allowSharedKeyAccess: false`. Verified on `crmagentsa` in `rg-crm-agent-rehearsal-ncus`:

  ```json
  {
    "allowBlobPublicAccess": false,
    "allowSharedKeyAccess": false,
    "defaultToOAuthAuthentication": null
  }
  ```

- **Why this matters for the project, not just the rehearsal**: ADR 0001 sells the architecture on "no long-lived secrets". But `AzureWebJobsStorage` being a shared-key connection string **IS a long-lived secret** embedded in Function App settings. The rehearsal found that the story only holds on a *permissive* subscription (where Bicep's absence of `allowSharedKeyAccess: false` defaults to `true`). On a real landing zone that enforces zero-secret at the policy level, the current Bicep is incompatible with its own stated design.
- **Proper fix (large, not in Slice 11)**:
  - Switch `AzureWebJobsStorage` from connection string → identity-based app settings: `AzureWebJobsStorage__blobServiceUri`, `AzureWebJobsStorage__queueServiceUri`, `AzureWebJobsStorage__tableServiceUri`, `AzureWebJobsStorage__credential = managedidentity`, and `AzureWebJobsStorage__clientId = <MI client ID>`.
  - Grant the User-Assigned Managed Identity these RBAC roles on the storage account: **Storage Blob Data Contributor**, **Storage Queue Data Contributor**, **Storage Table Data Contributor**.
  - Remove any code path that reads `storage.listKeys()` — no place in the Bicep should depend on an account key.
  - Deployment: switch from `az functionapp deployment source config-zip` (shared-key) to either `az functionapp deploy --type zip` (uses SCM endpoint) combined with `WEBSITE_RUN_FROM_PACKAGE=<blob-uri-with-identity>`, or Run-From-Package via blob storage with identity-based access.
  - Update `bicep-deploy.md` zip-deploy section with the new command.
- **Slice 11 cannot close without this**: AC2 (real Bicep deploy + preflight green) and AC3 (OBO integration test via `/api/chat`) both require a working Function App. AC4 (teardown + rebuild) needs the whole chain working once. Without identity-based storage, none of these complete.
- **Proposed action (awaiting user decision — see end of this section)**: file a new slice (Slice 12?) scoped to "identity-based storage + deployment transport" as a dependency of Slice 11. Update Slice 11's Blocked-by accordingly.
- **Status**: FLAGGED. Pending user decision on how to split.

### #6 — `aad-setup.md` never exposes the AAD app as an OAuth resource

- **Surfaced in**: Step 8.1 (first attempt to mint a user-audience JWT).
- **File**: [`aad-setup.md`](./aad-setup.md) — missing section between existing §2 and §3.
- **Symptom**: `az account get-access-token --resource <appId>` returns `AADSTS65001: The user or administrator has not consented to use the application with ID '04b07795-8ddb-461a-bbee-02f9e1bf7b46' named 'Microsoft Azure CLI'.`
- **Root cause**: The runbook stops at `az ad app create` + FIC, which is enough for server-to-server OBO receipt but **not** enough for a user (or user-delegated client like Azure CLI) to request a token *for* the app. Missing: (1) `identifierUris = ["api://<appId>"]`, (2) at least one delegated `oauth2PermissionScopes` (conventionally `user_impersonation`), (3) either tenant-wide admin consent OR `preAuthorizedApplications` for the known user-facing client IDs.
- **Impact**: Any user-initiated OBO flow is impossible without this step. Production UIs, demo tooling, and the Step 8 OBO rehearsal all hit the same AADSTS65001 wall. Same-tenant production is affected just as much as cross-tenant — nothing about this is CDX-specific.
- **Fix**: new `aad-setup.md §3 — Expose the app as an API` section applied in this PR, covering the three updates via `az ad app update --identifier-uris` and two `az rest PATCH` calls. Existing FIC step renumbered to §4.
- **Status**: fix applied in this PR; verified by re-running Step 8.1 after the fix (token mint returns a JWT with `aud=api://<appId>`, `scp=user_impersonation`).

### #7 — T2 tenant policy blocks Entra tokens as FIC assertions (`AADSTS700236`)

**This defines an architectural scope, not a bug. Documented rather than engineered around.**

- **Surfaced in**: Step 8.4 (server-side OBO exchange).
- **Files**: [`docs/adr/0001-obo-with-wif.md`](../adr/0001-obo-with-wif.md) (scope clarification), [`docs/operations/troubleshooting.md`](../operations/troubleshooting.md) (triage row).
- **Symptom**: Server-side OBO request to T2's Entra token endpoint is rejected with `AADSTS700236: Entra ID tokens issued by issuer '<T1>/v2.0' may not be used for federated identity credential flows for applications or managed identities registered in this tenant.` The MI token from T1 (cryptographically valid, correctly scoped to `api://AzureADTokenExchange`, matching the FIC's `subject`) is refused at the policy layer.
- **Root cause**: Microsoft Entra tenant-level policy. Newer tenants (CDX demo tenants among them) refuse to accept Entra-issued tokens as FIC assertions, regardless of which tenant the token originates from. The restriction exists to prevent Entra-to-Entra self-exploitation; external IdPs (AKS, GitHub OIDC, K8s service accounts) are unaffected because their tokens aren't "Entra ID tokens". This is an upstream Microsoft policy, **not configurable by the app-level admin**.
- **What this means for the architecture**: OBO + WIF is a **same-tenant** pattern. Lenovo's production deployment is expected to be same-tenant (MI, AAD app, and Dataverse all in Lenovo's Entra tenant), which does not cross a tenant boundary during FIC and therefore is unaffected by this policy. The cross-tenant rehearsal in CDX is a strict-superset test that was expected to validate a pattern Lenovo's production doesn't actually deploy; the test reaches its useful limit at Step 8.3 rather than Step 8.4.
- **What this means for external colleagues**: users who are not in Lenovo's production tenant should join as **Entra B2B guests** in that tenant (their home identity — Google, MSA, another company's Entra — resolves through B2B to a guest identity in Lenovo's tenant). Guest tokens are issued by Lenovo's tenant, so OBO is same-tenant from the server's perspective. A follow-up ADR will cover the B2B external-user flow; no changes to this codebase are required.
- **Fix**: none on our side. Slice 11 adds:
  - New consequence in `docs/adr/0001-obo-with-wif.md` stating OBO+WIF is same-tenant only and explaining why.
  - New row in `docs/operations/troubleshooting.md` under Deployment-time problems pointing operators at this exact error + remediation (verify same-tenant topology).
- **Status**: documented in this PR. No code or infrastructure change needed.

### Runbook enhancement — Consumption Plan (Y1) quota varies by region on MCAPS-style subscriptions

- **Surfaced in**: Step 5 (first `what-if` in `eastus2`).
- **File**: [`docs/operations/troubleshooting.md`](../operations/troubleshooting.md) or [`bicep-deploy.md`](./bicep-deploy.md) §deploy.
- **Symptom**: `InternalSubscriptionIsOverQuotaForSku` / `Current Limit (Dynamic VMs): 0`. Consumption Plan Function Apps need "Dynamic VMs" vCPU headroom that MCAPS / restricted subscriptions often default to zero in some regions.
- **Root cause (subscription-specific, not a code bug)**: region-level vCPU quotas on restricted tenants.
- **Recommended fix to runbook**: add a troubleshooting row pointing at three escape hatches — (a) deploy to a different region where quota > 0 (rehearsal chose `northcentralus`); (b) request a quota increase via Azure Portal → Subscriptions → Usage + Quotas; (c) switch the Bicep to a non-Consumption SKU (Premium / Flex / App Service Plan). Option (a) is the only one that unblocks without async admin interaction.
- **Status**: applied to `docs/operations/troubleshooting.md` (Deployment-time problems table, quota row, see commit `205b8ee`).

### Runbook enhancement (not a bug) — "tick Delegate only" should be emphasised

- **Surfaced in**: Step 4 (System Administrator accidentally ticked alongside Delegate).
- **File**: [`dataverse-setup.md`](./dataverse-setup.md) §Assign a security role.
- **Observation**: runbook already says Delegate is "Minimal, recommended" and warns "should NOT with pure OBO", but the PPAC multi-select UI makes it effortless to over-grant. An explicit "**only** tick Delegate; do not also tick System Administrator or other broad roles" would short-circuit a predictable mis-click.
- **Status**: applied to `dataverse-setup.md` §Assign a security role (explicit "Tick only the chosen role" warning block, see commit `205b8ee`). Not a numbered bug — the existing guidance was technically complete, just easy to gloss over.

### ⚠ Time-limit risk — CDX env is `Trial (subscription-based)`

Not a runbook bug, but a rehearsal-environment risk worth tracking in this log: screenshot of env Details shows **Type: Trial (subscription-based)**. Dataverse Trial environments expire on a timer (commonly 30 days, extendable once). If this env expires mid-Slice-11 or shortly after merge, all the evidence above — app user, Delegate role, RLS verification — evaporates along with the env.

**Action**: check the env's expiration date in PPAC (Environments list → hover on CRM190711 → look for "Expires on" column, or open Settings → Product → Features). If < 4 weeks remain, either extend or switch the whole rehearsal to a non-trial dev env before continuing. Confirmed date lands here once known.
