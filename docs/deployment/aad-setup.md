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
az ad app permission admin-consent --id <appId>
```

## 3. Wire the Federated Identity Credential

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

## 4. Verify

Run the pre-flight script from a shell with `AUTH_MODE=obo` set — see [preflight.md](./preflight.md). Expected output:

```
✓ token-acquisition            pass Entra issued a Dataverse-scoped access token
✓ dataverse-whoami             pass Dataverse accepted the token as UserId=...
```

If `token-acquisition` fails with `AADSTS700213`, the FIC audience is wrong for the cloud. Cross-reference the table above.

## 5. What to record for the project file

Hand off to the platform engineer (for Bicep `parameters.*.json`):

- `AAD_APP_CLIENT_ID` = `<appId>`
- `AAD_APP_TENANT_ID` = `<tenantId>`
- FIC wired and verified ✓
- Dataverse application user created ✓ (next: [dataverse-setup.md](./dataverse-setup.md))
