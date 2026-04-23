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

(pending)

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

### Runbook enhancement (not a bug) — "tick Delegate only" should be emphasised

- **Surfaced in**: Step 4 (System Administrator accidentally ticked alongside Delegate).
- **File**: [`dataverse-setup.md`](./dataverse-setup.md) §Assign a security role.
- **Observation**: runbook already says Delegate is "Minimal, recommended" and warns "should NOT with pure OBO", but the PPAC multi-select UI makes it effortless to over-grant. An explicit "**only** tick Delegate; do not also tick System Administrator or other broad roles" would short-circuit a predictable mis-click.
- **Status**: to apply as a small clarification in the same PR. Not a numbered bug — the existing guidance is technically complete, just easy to gloss over.

### ⚠ Time-limit risk — CDX env is `Trial (subscription-based)`

Not a runbook bug, but a rehearsal-environment risk worth tracking in this log: screenshot of env Details shows **Type: Trial (subscription-based)**. Dataverse Trial environments expire on a timer (commonly 30 days, extendable once). If this env expires mid-Slice-11 or shortly after merge, all the evidence above — app user, Delegate role, RLS verification — evaporates along with the env.

**Action**: check the env's expiration date in PPAC (Environments list → hover on CRM190711 → look for "Expires on" column, or open Settings → Product → Features). If < 4 weeks remain, either extend or switch the whole rehearsal to a non-trial dev env before continuing. Confirmed date lands here once known.
