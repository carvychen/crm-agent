# Global dev-tenant delivery rehearsal log

Slice 11 evidence. Captures the commands and real outputs from walking the customer runbook ([aad-setup.md](./aad-setup.md), [dataverse-setup.md](./dataverse-setup.md), [bicep-deploy.md](./bicep-deploy.md), [preflight.md](./preflight.md)) against an accessible Global dev tenant — **before** Lenovo runs the same runbook in their inaccessible China tenant.

The rehearsal closes two gaps PRD #2 could not close from the inside:

1. OBO-over-WIF was never exercised against real Entra + real Dataverse (`AUTH_MODE=app_only_secret` in `tests/integration/` used `client_credentials`).
2. `infra/main.bicep` was only validated via `what-if`; never `az deployment group create`'d.

See [issue #24](https://github.com/carvychen/crm-agent/issues/24) for scope and acceptance criteria.

## Topology

This rehearsal spans **two tenants** because the author's dev environment is split across them. Lenovo's production deployment is more likely **same-tenant** (AAD app + MI + Azure subscription all in one tenant); the cross-tenant setup is a strict superset — if it works here, same-tenant works modulo the FIC issuer/subject simplifications called out in the [same-tenant vs cross-tenant appendix](#same-tenant-vs-cross-tenant-appendix) below.

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

## Step 6 — Wire FIC on T2 app (pointing at T1 MI)

(pending — executes after Step 5)

## Step 7 — Preflight against the real deployment

Runbook reference: [preflight.md](./preflight.md).

(pending)

## Step 8 — Real-OBO integration test (two users, RLS)

(pending — closes ADR 0007 Known gap)

## Step 9 — Teardown + rebuild proof

(pending — AC4)

## Same-tenant vs cross-tenant appendix

(to fill in from runbook observations once steps 1–7 are complete)

## Runbook bugs discovered

The point of the rehearsal is to find these. Each bug here is fixed in the referenced runbook file within this same PR; the teardown + rebuild pass (AC4) confirms the fix.

### #1 — Entra replication race between `permission add` and `admin-consent`

- **Surfaced in**: Step 2 (first execution).
- **File**: [`aad-setup.md`](./aad-setup.md) §2.
- **Symptom**: `az ad app permission admin-consent` returns `Request_BadRequest` / `"application '<appId>' you are trying to use has been removed or is configured to use an incorrect application identifier."`, even though `az ad app show` and `az ad sp show` both succeed against the same `appId` milliseconds earlier.
- **Root cause**: Entra tenant replication lag. `admin-consent` internally routes through AAD Graph (a different backend from the Microsoft Graph endpoint `az ad app create` / `az ad sp create` use), which has not yet picked up the new app + SP.
- **Reproduction**: ~30–60 s window after app+SP creation, 100% reproducible from a cold tenant.
- **Fix**: insert a 30-second wait (or a bounded retry loop) between `az ad app permission add` and `az ad app permission admin-consent` in `aad-setup.md` §2. Retry loop is preferred — it degrades gracefully under slower tenants.
- **Status**: to apply to `aad-setup.md` in this PR; verified re-green by AC4 (teardown + rebuild).

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
- **Status**: to apply to `dataverse-setup.md` in this PR; verified by AC4 (teardown + rebuild).

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
- **Status**: fix in this PR; verified by AC4 (teardown + rebuild produces a working deployment).

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

### Runbook enhancement — Consumption Plan (Y1) quota varies by region on MCAPS-style subscriptions

- **Surfaced in**: Step 5 (first `what-if` in `eastus2`).
- **File**: [`docs/operations/troubleshooting.md`](../operations/troubleshooting.md) or [`bicep-deploy.md`](./bicep-deploy.md) §deploy.
- **Symptom**: `InternalSubscriptionIsOverQuotaForSku` / `Current Limit (Dynamic VMs): 0`. Consumption Plan Function Apps need "Dynamic VMs" vCPU headroom that MCAPS / restricted subscriptions often default to zero in some regions.
- **Root cause (subscription-specific, not a code bug)**: region-level vCPU quotas on restricted tenants.
- **Recommended fix to runbook**: add a troubleshooting row pointing at three escape hatches — (a) deploy to a different region where quota > 0 (rehearsal chose `northcentralus`); (b) request a quota increase via Azure Portal → Subscriptions → Usage + Quotas; (c) switch the Bicep to a non-Consumption SKU (Premium / Flex / App Service Plan). Option (a) is the only one that unblocks without async admin interaction.
- **Status**: to apply as a troubleshooting-table row in `docs/operations/troubleshooting.md` later in this PR.

### Runbook enhancement (not a bug) — "tick Delegate only" should be emphasised

- **Surfaced in**: Step 4 (System Administrator accidentally ticked alongside Delegate).
- **File**: [`dataverse-setup.md`](./dataverse-setup.md) §Assign a security role.
- **Observation**: runbook already says Delegate is "Minimal, recommended" and warns "should NOT with pure OBO", but the PPAC multi-select UI makes it effortless to over-grant. An explicit "**only** tick Delegate; do not also tick System Administrator or other broad roles" would short-circuit a predictable mis-click.
- **Status**: to apply as a small clarification in the same PR. Not a numbered bug — the existing guidance is technically complete, just easy to gloss over.

### ⚠ Time-limit risk — CDX env is `Trial (subscription-based)`

Not a runbook bug, but a rehearsal-environment risk worth tracking in this log: screenshot of env Details shows **Type: Trial (subscription-based)**. Dataverse Trial environments expire on a timer (commonly 30 days, extendable once). If this env expires mid-Slice-11 or shortly after merge, all the evidence above — app user, Delegate role, RLS verification — evaporates along with the env.

**Action**: check the env's expiration date in PPAC (Environments list → hover on CRM190711 → look for "Expires on" column, or open Settings → Product → Features). If < 4 weeks remain, either extend or switch the whole rehearsal to a non-trial dev env before continuing. Confirmed date lands here once known.
