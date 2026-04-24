# Bicep Deployment

**Audience**: Lenovo's platform engineer. Run after [aad-setup.md](./aad-setup.md) and [dataverse-setup.md](./dataverse-setup.md) are complete — Bicep sets up Azure resources but cannot create AAD apps or Dataverse users.

**Output**: a running Function App serving the MCP server (and optionally the reference agent's `/api/chat`) in your target Azure cloud.

## Before you start

Have these handy:

| Value | Where from |
|---|---|
| Resource group name (create if needed) | Your call; must exist in the target cloud / region |
| `AAD_APP_CLIENT_ID` | [aad-setup.md](./aad-setup.md) step 1 |
| `AAD_APP_TENANT_ID` | [aad-setup.md](./aad-setup.md) step 1 |
| `DATAVERSE_URL` | D365 Admin Center → environment → URL |
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry portal → project → Overview (only if `ENABLE_REFERENCE_AGENT=true`) |
| Cloud | `global` (Azure Public) or `china` (21Vianet) |

## Edit the parameter file

Open `infra/parameters.global.json` or `infra/parameters.china.json` and replace every `REPLACE*` placeholder.

`CLOUD_ENV` inside the file must match the cloud you are logged into (`global` → `az cloud set --name AzureCloud`, `china` → `az cloud set --name AzureChinaCloud`). A mismatch deploys fine but produces a Function App that can never acquire a token.

## Validate before deploying (recommended)

```bash
az deployment group what-if \
  --resource-group <your-rg> \
  --template-file infra/main.bicep \
  --parameters infra/parameters.global.json
```

Review the output. **Resources marked `Delete`** mean the deployment will remove pre-existing resources — make sure that's intentional (usually only a concern when redeploying into a used RG).

## Deploy

```bash
az deployment group create \
  --resource-group <your-rg> \
  --name crm-agent-<env>-$(date +%Y%m%d-%H%M) \
  --template-file infra/main.bicep \
  --parameters infra/parameters.global.json
```

Record the deployment name; you'll use it to read outputs in a moment.

## Wire the Federated Identity Credential

The Bicep emits the Managed Identity's `principalId` as an output, but cannot create the FIC on the AAD app (which lives in Entra, not in the subscription). Loop back to [aad-setup.md](./aad-setup.md) step 3 now with that output in hand:

```bash
az deployment group show \
  --resource-group <your-rg> \
  --name <deployment-name> \
  --query properties.outputs.managedIdentityPrincipalId.value -o tsv
```

## Zip-deploy the Function App code

```bash
# From the repo root:
zip -r /tmp/crm-agent.zip . \
  -x ".env" ".env.example" \
     ".venv/*" ".git/*" ".github/*" ".claude/*" \
     "tests/*" "docs/*" "infra/*" "scripts/*" "assets/*" "skills/*" \
     "agent.py" \
     "__pycache__/*" "*/__pycache__/*" \
     ".pytest_cache/*" "*.pyc" "*.DS_Store"

az functionapp deployment source config-zip \
  --resource-group <your-rg> \
  --name $(az deployment group show -g <your-rg> -n <deployment-name> --query properties.outputs.functionAppName.value -o tsv) \
  --src /tmp/crm-agent.zip
```

> On Flex Consumption (the hosting model per [ADR 0008](../adr/0008-identity-based-storage.md)), `config-zip` **automatically** uploads the package to the blob container declared in `functionAppConfig.deployment.storage` using the site's User-Assigned Managed Identity — no shared-key, no SCM-basic-auth dependency. The same command on Linux Consumption (Y1, deprecated) would have failed on a policy-locked subscription. `allowSharedKeyAccess: false` on the storage account enforces this as a hard guarantee.

Wait ~60 seconds for the first cold start, then:

```bash
python scripts/preflight.py
```

Expected — four green ticks. If any fail, the output's `remediation:` line names the concrete next step; cross-reference [troubleshooting.md](../operations/troubleshooting.md) for deeper help.

## Cost note

Consumption plan (Y1) + Log Analytics (PerGB2018, 30-day retention) + App Insights + default alerts runs **under ~$10/month at 100 calls/day**. LLM inference (Foundry) is separate and billed by token per your deployment.

## Redeploy

Redeploys are idempotent — re-run the same command. The `name` timestamp means each deployment has its own record in the RG's deployment history (useful for rollback auditing).

## Tear down

```bash
az group delete --resource-group <your-rg>
```

Does not remove the AAD app or the Dataverse application user — both are in separate tenants / environments.
