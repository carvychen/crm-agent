# Dataverse Setup

**Audience**: Lenovo's Dynamics 365 administrator. Execute after [aad-setup.md](./aad-setup.md) has registered the AAD application.

**Output**: a Dataverse **application user** that the MCP server calls Dataverse as, with a security role granting the **Delegate** privilege so OBO works.

## Why an application user

OBO means the MCP server acts on behalf of the real end user. That still requires the AAD app to be **a known principal inside Dataverse** — an "application user" record linked to the AAD app's client ID. Without it, Dataverse rejects even a perfectly valid Entra token with `401` or `403`.

## Create the application user

D365 Admin Center is the supported path; there is no Bicep / Graph equivalent.

1. Go to [https://admin.powerplatform.microsoft.com](https://admin.powerplatform.microsoft.com) (or [https://admin.powerplatform.microsoft.cn](https://admin.powerplatform.microsoft.cn) in China)
2. **Environments** → pick your environment → **Settings** → **Users + permissions** → **Application users** → **+ New app user**
3. **+ Add an app** → search for the AAD app's client ID (from [aad-setup.md](./aad-setup.md)'s step 1)
4. **Business unit**: pick the root business unit unless Lenovo's Dataverse admin tells you otherwise
5. Click **Create**

## Assign a security role

The default new application user has **no privileges**. Without at least one security role granting the **Delegate** privilege (`prvActOnBehalfOfAnotherUser`), OBO calls return 403 at the Dataverse boundary.

Lenovo-approved options:

- **Delegate** (built-in) — grants only `prvActOnBehalfOfAnotherUser`. Minimal, recommended.
- A custom role cloned from **Delegate** + whatever additional read/write Lenovo's data governance team approves. Required if the application user itself also needs to read/write data directly (which it should NOT with pure OBO — OBO runs as the real user and uses the user's privileges).

Assign in the same admin UI: the application user row → **Manage roles** → tick the chosen role → **Save**.

## Verify

```bash
python scripts/preflight.py
```

Expected:

```
✓ dataverse-whoami             pass Dataverse accepted the token as UserId=<GUID>
```

The `<GUID>` echoed back is the Dataverse **systemuser.SystemUserId** of the application user you just created. Confirm it matches what D365 Admin Center shows — a mismatch means a different application user than expected is handling the calls.

## Common failure modes

| preflight output | Probable cause | Fix |
|---|---|---|
| `Dataverse returned 401` | Application user does not exist, OR AAD app `client_id` != the one the user is linked to | Re-check step 3 in D365 Admin Center |
| `Dataverse returned 403` | Application user exists but has no Delegate-capable role | Assign the Delegate role (step 2) |
| `Dataverse returned 404` for WhoAmI | `DATAVERSE_URL` points at the wrong environment | Fix the parameter file; the env's URL is visible in the admin portal |

## What to record

- Application user created ✓ (note the SystemUserId for the operations handoff)
- Security role assigned ✓ (note the role name)
- Preflight's `dataverse-whoami` check passes ✓
