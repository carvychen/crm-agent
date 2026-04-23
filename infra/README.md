# Bicep infrastructure

Deploys the MCP server + reference agent as one Function App per ADR 0002 (self-hosted MCP SDK) and ADR 0004 (HTTP transport between agent and MCP).

## Layout

```
infra/
├── main.bicep                   # root orchestrator
├── parameters.global.json       # Azure Global / Public values
├── parameters.china.json        # Azure China / 21Vianet values
└── modules/
    ├── monitoring.bicep         # Log Analytics + Application Insights
    ├── identity.bicep           # User-Assigned Managed Identity
    ├── function-app.bicep       # Storage + Consumption plan + Function App + app settings
    └── alerts.bicep             # 4 default alerts (5xx, p95 latency, auth failure, /api/chat 4xx)
```

## Deploy

```bash
# fill in dataverseUrl / aadApp* / foundryProjectEndpoint first
az deployment group create \
  --resource-group <your-rg> \
  --template-file infra/main.bicep \
  --parameters infra/parameters.global.json
```

For Azure China, swap `parameters.global.json` for `parameters.china.json`.

## Validate without deploying

```bash
az bicep build --file infra/main.bicep                     # syntax only
az deployment group what-if \
  --resource-group <scratch-rg> \
  --template-file infra/main.bicep \
  --parameters infra/parameters.global.json                # full shape validation
```

CI runs both automatically on every PR; `what-if` is gated on the repo secret `BICEP_WHATIF_RESOURCE_GROUP`.

## Post-deploy steps (not in Bicep)

These cannot be set up automatically from this template and must be done once per environment:

1. **Federated Identity Credential on the AAD app** — register the Function App's Managed Identity as a FIC on the AAD app so OBO can run without client secrets (ADR 0001). The MI's `principalId` + `clientId` are Bicep outputs; use them in your Entra portal / az CLI for the FIC creation.

2. **Dataverse application user** — link the AAD app's client ID to the Dataverse environment (D365 Admin Center → Users + permissions → Application users) with a security role granting Delegate privilege.

3. **Cognitive Services User role on Foundry** — if Foundry is in the **same** tenant as the Function App's MI: `az role assignment create --assignee-object-id <mi-principal-id> --assignee-principal-type ServicePrincipal --role "Cognitive Services User" --scope <foundry-project-resource-id>`. If Foundry is in a **different** tenant (e.g. author's personal Azure, Lenovo-approved CN tenant), wire a cross-tenant service principal instead and set `FOUNDRY_AZURE_*` env vars — see `tests/integration/test_foundry_live.py` for the pattern.

After all three, run `python scripts/preflight.py` against the deployed Function App to confirm the chain end-to-end.
