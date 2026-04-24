# AAD Setup

**Audience**: Lenovo's identity administrator. You execute this once per deployment target (dev, UAT, prod), **before** the Bicep deployment in [bicep-deploy.md](./bicep-deploy.md).

**Output**: an AAD application registration with delegated Dataverse permissions and a Federated Identity Credential that trusts a Managed Identity — no long-lived client secret anywhere.

**ADR references**: [0001 OBO with WIF](../adr/0001-obo-with-wif.md) explains why. [0003 Dual-cloud parity](../adr/0003-dual-cloud-parity.md) drives the `CLOUD_ENV`-specific values below.

## 1. Register the AAD application

```bash
az ad app create \
  --display-name "crm-agent-<env>" \
  --sign-in-audience AzureADMyOrg
```

Record `appId` (this becomes `AAD_APP_CLIENT_ID` in the Bicep parameter file) and the tenant ID (`AAD_APP_TENANT_ID`).

Create the corresponding service principal:

```bash
az ad sp create --id <appId>
```

## 2. Grant delegated Dynamics permission

In the Entra portal → App registrations → your app → **API permissions**:

1. Add a permission → **Dynamics CRM** → **Delegated permissions** → `user_impersonation`
2. Grant admin consent

Or via CLI:

```bash
az ad app permission add \
  --id <appId> \
  --api 00000007-0000-0000-c000-000000000000 \
  --api-permissions 78ce3f0f-a1ce-49c2-8cde-64b5c0896db4=Scope

# Entra replication race: `admin-consent` can fail immediately after a fresh
# `app create` + `sp create` + `permission add` with
#   "application '<appId>' you are trying to use has been removed or is
#    configured to use an incorrect application identifier"
# even though `az ad app show` and `az ad sp show` both succeed. The
# admin-consent endpoint routes through AAD Graph, which replicates the
# new app/SP 30–60 s after creation. Retry with bounded backoff.
for i in 1 2 3 4 5 6; do
  az ad app permission admin-consent --id <appId> && break
  echo "admin-consent attempt $i failed (likely Entra replication); retrying in 10s…"
  sleep 10
done
```

## 3. Expose the app as an OAuth resource

Step 2 lets the app **request** a delegated Dataverse permission downstream. Step 3 (this one) lets client apps **request a user token for this AAD app** — without it, `az account get-access-token --resource <appId>` and equivalent calls from the UI fail with `AADSTS65001: The user or administrator has not consented to use the application …`. Every end-user / agent login flow relies on this step.

```bash
OBJECT_ID=$(az ad app show --id <appId> --query id -o tsv)
SCOPE_ID=$(uuidgen)       # one-shot UUID for the user_impersonation scope
AZ_CLI_APP_ID=04b07795-8ddb-461a-bbee-02f9e1bf7b46   # Microsoft Azure CLI (well-known)

# 3.1 Identifier URI — the `api://<appId>` convention.
az ad app update --id <appId> --identifier-uris "api://<appId>"

# 3.2 Delegated scope users consent to. `user_impersonation` is the convention.
az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/$OBJECT_ID" \
  --headers "Content-Type=application/json" \
  --body "$(cat <<JSON
{
  "api": {
    "oauth2PermissionScopes": [
      {
        "id": "$SCOPE_ID",
        "value": "user_impersonation",
        "type": "User",
        "isEnabled": true,
        "adminConsentDisplayName": "Access CRM Agent as user",
        "adminConsentDescription": "Allow the CRM Agent API to act on behalf of the signed-in user.",
        "userConsentDisplayName": "Access CRM Agent as you",
        "userConsentDescription": "Allow the CRM Agent API to act on your behalf."
      }
    ]
  }
}
JSON
)"

# 3.3 Pre-authorize the Azure CLI (so users can `az account get-access-token`
#     without hitting an individual consent prompt). Add further `preAuthorizedApplications`
#     entries for any UI SPA / native client appIds that legitimately need to
#     request tokens for this AAD app on behalf of the user.
az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/$OBJECT_ID" \
  --headers "Content-Type=application/json" \
  --body "$(cat <<JSON
{
  "api": {
    "preAuthorizedApplications": [
      {
        "appId": "$AZ_CLI_APP_ID",
        "delegatedPermissionIds": ["$SCOPE_ID"]
      }
    ]
  }
}
JSON
)"
```

Verify:

```bash
az ad app show --id <appId> --query "{identifierUris:identifierUris, scopes:api.oauth2PermissionScopes[].value, preAuth:api.preAuthorizedApplications[].appId}"
```

Expect `identifierUris: ["api://<appId>"]`, `scopes: ["user_impersonation"]`, and the Azure CLI appId under `preAuth`.

## 4. Wire the Federated Identity Credential

Bicep deploys a User-Assigned Managed Identity and emits its `principalId` as an output. After the Bicep deployment succeeds, register that MI as a FIC on this AAD app so OBO can use the MI as its `client_assertion`.

**The FIC audience differs by cloud** (this is the single most common CN deployment mistake):

| `CLOUD_ENV` | FIC audience |
|---|---|
| `global` | `api://AzureADTokenExchange` |
| `china` | `api://AzureADTokenExchangeChina` |

```bash
MI_PRINCIPAL_ID=$(az deployment group show \
  --resource-group <rg> \
  --name <deployment-name> \
  --query properties.outputs.managedIdentityPrincipalId.value -o tsv)

# Construct the issuer URL from your tenant.
TENANT_ID=<AAD_APP_TENANT_ID>
ISSUER="https://login.microsoftonline.com/$TENANT_ID/v2.0"  # global
# For china: ISSUER="https://login.partner.microsoftonline.cn/$TENANT_ID/v2.0"

AUDIENCE="api://AzureADTokenExchange"          # global
# For china: AUDIENCE="api://AzureADTokenExchangeChina"

az ad app federated-credential create \
  --id <appId> \
  --parameters @- <<JSON
{
  "name": "crm-agent-mi",
  "issuer": "$ISSUER",
  "subject": "$MI_PRINCIPAL_ID",
  "audiences": ["$AUDIENCE"]
}
JSON
```

## 5. Verify

Run the pre-flight script from a shell with `AUTH_MODE=obo` set — see [preflight.md](./preflight.md). Expected output:

```
✓ token-acquisition            pass Entra issued a Dataverse-scoped access token
✓ dataverse-whoami             pass Dataverse accepted the token as UserId=...
```

If `token-acquisition` fails with `AADSTS700213`, the FIC audience is wrong for the cloud. Cross-reference the table above.

## 6. What to record for the project file

Hand off to the platform engineer (for Bicep `parameters.*.json`):

- `AAD_APP_CLIENT_ID` = `<appId>`
- `AAD_APP_TENANT_ID` = `<tenantId>`
- FIC wired and verified ✓
- Dataverse application user created ✓ (next: [dataverse-setup.md](./dataverse-setup.md))
