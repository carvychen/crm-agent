# OBO with Workload Identity Federation

The CRM is governed by Dataverse row-level security and Lenovo's compliance program treats shared service-account impersonation as a privileged-access audit finding. The MCP server therefore authenticates to Dataverse via OAuth 2.0 On-Behalf-Of flow using Workload Identity Federation — no long-lived client secret, each Dataverse call runs under the real user's identity, RLS applies natively, and audit logs stay correct.

## Considered Options

- **Single service account with broad privileges** — RLS bypassed, audit logs show only the service account. Not viable for Lenovo's compliance posture.
- **`CallerObjectId` impersonation** — technically preserves audit and RLS, but `prvActOnBehalfOfAnotherUser` is an unbounded privileged right on a single account; compliance will flag it as a critical control gap.
- **OBO with `client_secret`** — works, but introduces a long-lived credential that needs Key Vault storage plus a rotation process. WIF removes the credential entirely.

## Consequences

- Adds ~2 weeks to Phase 1 for AAD app registration, delegated Dataverse permissions, and FIC setup — mostly waiting on Lenovo's identity admin team, not engineering effort.
- FIC audience is cloud-specific: `api://AzureADTokenExchange` (global) vs `api://AzureADTokenExchangeChina` (CN). Must be configured per `CLOUD_ENV`.
- Agents (reference and external) must obtain user tokens with audience equal to the MCP AAD app's Application ID URI. UI integrators need to know this.
- Dataverse must have an application user linked to the AAD app with a security role whose privileges are the intersection the MCP server is allowed to exercise; users with fewer privileges can still call through but receive RLS-filtered results.
- **The zero-secret claim also covers `AzureWebJobsStorage`.** The Function App's backing storage account is accessed via the same Managed Identity plus data-role RBAC — no account key is stored as a Function App setting. See [ADR 0008](./0008-identity-based-storage.md); this was made explicit after Slice 11's delivery rehearsal proved the original Bicep silently relied on a shared-key connection string.
- **OBO + WIF is a same-tenant architectural pattern.** The Managed Identity and the AAD app registration must live in the same Microsoft Entra tenant. Cross-tenant deployments (MI in tenant A, AAD app in tenant B) are blocked at the Entra policy layer: the inviting tenant refuses Entra-issued tokens as FIC assertions, yielding `AADSTS700236` at runtime. Slice 11's delivery rehearsal (Dynamics tenant ≠ Azure subscription tenant, by necessity of the author's Microsoft-internal setup) hit this boundary at Step 8 and confirmed it is an upstream Entra policy, not a code or configuration issue. Lenovo's expected production deployment is same-tenant and therefore unaffected. Colleagues who are not in the production Entra tenant — partners, contractors, subsidiaries — should join via **Entra B2B guest invitations** in the production tenant; their tokens are then issued by the production tenant and OBO remains same-tenant from the server's perspective. The rehearsal log (`docs/deployment/rehearsal-global.md` §8) and troubleshooting runbook capture the error and its remediation.
