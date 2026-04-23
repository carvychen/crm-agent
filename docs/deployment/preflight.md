# Pre-flight Validation

**Audience**: anyone who just changed configuration (identity admin, D365 admin, platform engineer). Run before you believe a deployment is working.

**Output**: four structured checks with pass / fail / skip — and for every fail, a `remediation:` line naming the concrete next step.

`scripts/preflight.py` is the "delivered blind" gate (Invariant 4): the authors have no access to your Azure China tenant, so every failure that we can anticipate is encoded here. See [ADR 0007](../adr/0007-testing-discipline.md) for the full discipline.

## Run

```bash
python scripts/preflight.py                # human-readable
python scripts/preflight.py --format json  # machine-parseable
```

Required env vars (or `.env` entries):

| Variable | Value |
|---|---|
| `CLOUD_ENV` | `global` or `china` |
| `AUTH_MODE` | `obo` in production, `app_only_secret` for dev |
| `DATAVERSE_URL` | Your Dataverse environment URL |
| `AAD_APP_CLIENT_ID` / `AAD_APP_TENANT_ID` | From [aad-setup.md](./aad-setup.md) |
| `FOUNDRY_PROJECT_ENDPOINT` / `FOUNDRY_MODEL` | When `ENABLE_REFERENCE_AGENT=true` |

When running **locally** from the repo, preflight auto-loads `skills/crm-opportunity/.env` and `.env` if present. In **production** (on the Function App) the env vars come from App Settings deployed by Bicep.

## Expected output

All green on a correctly configured deployment:

```
✓ dns-reachability             pass resolved 3 host(s): login.microsoftonline.com, ...
✓ token-acquisition            pass Entra issued a Dataverse-scoped access token
✓ dataverse-whoami             pass Dataverse accepted the token as UserId=<GUID>
✓ foundry-reachability         pass Foundry returned a reply (33 chars)

4 passed · 0 failed · 0 skipped
```

## Failure → remediation

The full remediation tree lives in the code (`src/preflight/checks.py`), but here's the quick map:

| Failing check | Most common cause | First thing to try |
|---|---|---|
| `dns-reachability` | Private DNS zone / firewall egress missing for the Function App | Verify name resolution from **inside** the Function App's VNet (if integrated); check hub-spoke Private Endpoint wiring |
| `token-acquisition` | AAD app missing, wrong `AZURE_CLIENT_SECRET`, or **wrong FIC audience for the cloud** | Compare the FIC audience against [aad-setup.md](./aad-setup.md) step 3 table; look for an `AADSTS` code in the `detail:` line |
| `dataverse-whoami` | Dataverse application user not created, OR missing Delegate privilege | Re-walk [dataverse-setup.md](./dataverse-setup.md); the `detail:` line's HTTP code distinguishes these (401 vs 403) |
| `foundry-reachability` | Cross-tenant credential, missing `Cognitive Services User` role, or wrong `FOUNDRY_MODEL` | See [infra/README.md](../../infra/README.md) post-deploy step 3 for the cross-tenant SP pattern |

Never skipped in prod: any check in `skip` state on a `Production` environment points to misconfiguration (usually `ENABLE_REFERENCE_AGENT=false` silently failing a check that should have run).

## In CI

`tests/integration/test_preflight_live.py` runs `scripts/preflight.py` as a subprocess on every PR against the author's Azure Global dev tenant (ADR 0007). If the test fails on a PR that does not touch preflight, the dev environment has drifted and needs human investigation before merging.
