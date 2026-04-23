# Secret Rotation

**Audience**: SRE / platform engineer. Fulfils US 31 — every credential this system relies on has a documented rotation procedure, even when the credential is "there is no credential" (WIF).

## What this system does NOT have to rotate

Per [ADR 0001](../adr/0001-obo-with-wif.md), production explicitly eliminates long-lived client secrets. The runtime has:

- **No `AZURE_CLIENT_SECRET`** in production (`AUTH_MODE=obo` refuses to boot under it)
- **No Key Vault-stored secret** — Bicep doesn't deploy one
- **No connection-string secret** — Storage uses Managed Identity; App Insights uses connection string but that's a telemetry pointer, not a credential

What DOES need periodic attention:

## 1. Federated Identity Credential

**What it is**: the trust relationship linking a Managed Identity to the AAD app, allowing OBO without a client secret.

**Lifecycle**: FICs don't expire on their own. Rotation is only needed when:

- The Managed Identity is recreated (e.g. deleting the RG and redeploying) — the new MI has a new `principalId`, so the old FIC no longer matches
- Compromise is suspected — delete + recreate forces re-issuance

**Rotate**:

```bash
# List existing FICs on the AAD app
az ad app federated-credential list --id <appId>

# Delete the stale one
az ad app federated-credential delete --id <appId> --federated-credential-id <fic-id>

# Recreate against the new MI principal — re-walk aad-setup.md step 3
```

No downtime window: preflight shows `token-acquisition` fail during the gap, but outbound user requests fail closed rather than leaking.

## 2. Managed Identity

**What it is**: the Azure-managed principal the Function App runs as.

**Lifecycle**: MIs don't have rotatable credentials by design — that's the whole point of WIF. The MI's internal tokens rotate automatically on every `get_token()`.

**Replacement** (rare — only when changing MI assignment, not really "rotation"):

```bash
# Detach the current MI from the Function App
az functionapp identity remove \
  --resource-group <rg> \
  --name <function-app-name> \
  --identities <current-mi-resource-id>

# Attach a new one — usually via redeploying Bicep with the new identity resource
az deployment group create ...
```

Remember to recreate the FIC against the new MI's `principalId` afterwards (see section 1).

## 3. Dataverse security role

**What it is**: the Delegate-capable security role assigned to the AAD application user.

**Lifecycle**: role membership persists until explicitly removed in D365 Admin Center. Lenovo's access-governance policy may require periodic re-attestation (typically quarterly).

**Re-attest**:

1. D365 Admin Center → environment → Users + permissions → Application users
2. Find the AAD app's application user
3. Manage roles → confirm the Delegate role is still assigned with the correct scope
4. Evidence the audit: screenshot, or export with `az dataverse ...`

**Change the role** (e.g. tightening privileges): assign the new role, verify preflight's `dataverse-whoami` still passes, then remove the old role. Do it in that order so there's never a window where the application user has zero roles.

## 4. Foundry service principal (when cross-tenant)

Applies only when Foundry is in a different tenant from the Function App's MI — the setup documented in `infra/README.md` and `tests/integration/test_foundry_live.py`.

**What it is**: `FOUNDRY_AZURE_TENANT_ID` / `FOUNDRY_AZURE_CLIENT_ID` / `FOUNDRY_AZURE_CLIENT_SECRET`, stored as App Settings on the Function App and as GitHub repo secrets for CI.

**Lifecycle**: this IS a long-lived client secret by necessity (cross-tenant RBAC limitation, not a design choice we made). Entra supports client secrets up to 2 years; Lenovo's policy may require shorter.

**Rotate** (example: every 90 days):

```bash
# Create a new secret, keep the old one active during the swap
NEW_SECRET=$(az ad app credential reset \
  --id <foundry-sp-appid> \
  --display-name "crm-agent-rotation-$(date +%Y%m%d)" \
  --query password -o tsv)

# Push the new value into the Function App
az functionapp config appsettings set \
  --resource-group <rg> \
  --name <function-app-name> \
  --settings "FOUNDRY_AZURE_CLIENT_SECRET=$NEW_SECRET"

# Verify it works
python scripts/preflight.py  # foundry-reachability must pass

# Now remove the OLD secret (get the keyId from `az ad app credential list`)
az ad app credential delete --id <foundry-sp-appid> --key-id <old-secret-keyid>

# Update GitHub repo secret FOUNDRY_AZURE_CLIENT_SECRET with $NEW_SECRET
```

## 5. GitHub Actions secrets

Used only in CI, not in production. Every secret listed in `.github/workflows/ci.yml` has the same rotation cadence as its Azure source of truth:

| Secret | Source | Rotate with |
|---|---|---|
| `AZURE_CLIENT_SECRET` | Dataverse SP | Section 2 of this doc (when Dataverse SP rotates) |
| `FOUNDRY_AZURE_CLIENT_SECRET` | Foundry SP | Section 4 |
| `AZURE_BICEP_*` (OIDC) | Federated credential | `az ad app federated-credential` on the Bicep CI app — similar pattern to section 1 |

GitHub's UI: Settings → Secrets and variables → Actions.

## Rotation schedule (recommended)

| Item | Frequency | Triggered automatically? |
|---|---|---|
| FIC | Never (unless MI changes) | No |
| MI | Never (tokens auto-rotate inside Azure) | Yes (internal) |
| Dataverse role re-attestation | Quarterly | No (compliance check) |
| Foundry SP secret (when cross-tenant) | ≤ 90 days | No |
| GitHub Actions secrets | With their source | No |

## Verification after any rotation

```bash
python scripts/preflight.py
```

If any check fails, roll back the change and investigate before proceeding — partial credentials are worse than old ones.
