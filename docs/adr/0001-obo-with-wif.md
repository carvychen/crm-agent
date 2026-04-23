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
