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

> **Tick only the chosen role.** The Manage-roles UI is multi-select, making it easy to accidentally tick System Administrator alongside Delegate. Don't. Any extra role over-grants the application user: it masks real permission requirements in the `AUTH_MODE=app_only_secret` dev path (which runs *as* the application user, not OBO), and it violates [ADR 0001](../adr/0001-obo-with-wif.md)'s least-privilege intent.

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

- **Application user's SystemUserId GUID** — for operations handoff and audit-trail correlation. Power Platform Admin Center does **not** expose this field in its Application users UI (only display name, App ID, and synthetic email). Retrieve it via the Dataverse Web API:

  ```bash
  TOKEN=$(az account get-access-token --resource "$DATAVERSE_URL" --query accessToken -o tsv)
  curl -s -H "Authorization: Bearer $TOKEN" \
       "$DATAVERSE_URL/api/data/v9.2/systemusers?\$filter=applicationid%20eq%20$AAD_APP_CLIENT_ID&\$select=systemuserid,applicationid,fullname" \
    | python3 -m json.tool
  ```

  Prerequisite: the admin running this command must themselves be a Dataverse user in this environment. Tenant admins are typically auto-provisioned; otherwise the call returns 401.

- **Security role name(s)** assigned to the application user — same handoff.
- **Preflight's `dataverse-whoami` check** passes, and the `UserId=<GUID>` it echoes matches the `systemuserid` above.
