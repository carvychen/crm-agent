---
name: crm-opportunity
description: Manage Dynamics 365 CRM opportunities including listing, searching, creating, updating, and deleting deals. Use when user mentions CRM opportunities, sales pipeline, deals, revenue, or asks to "list opportunities", "create a deal", "update opportunity", or "delete deal".
license: MIT
compatibility: Requires Python 3.10+, azure-identity, requests, python-dotenv. Needs DATAVERSE_URL, AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET environment variables.
metadata:
  author: carvychen
  version: "2.0"
---

# CRM Opportunity Management

Manage Dynamics 365 CRM opportunities via the Dataverse Web API.

## Available Scripts

| Script | Description | Required Args | Optional Args |
|--------|-------------|---------------|---------------|
| `scripts/search_accounts.py` | Search accounts by name | `--name` | |
| `scripts/search_contacts.py` | Search contacts by name | `--name` | |
| `scripts/list_opportunities.py` | Query opportunities | | `--filter`, `--order-by`, `--top` |
| `scripts/get_opportunity.py` | Get one opportunity | `--opportunity-id` | |
| `scripts/create_opportunity.py` | Create an opportunity | `--name`, `--account-id` | `--contact-id`, `--estimatedvalue`, `--estimatedclosedate`, `--closeprobability`, `--opportunityratingcode`, `--parentcontactid` |
| `scripts/update_opportunity.py` | Update an opportunity | `--opportunity-id` | `--name`, `--estimatedvalue`, `--estimatedclosedate`, `--closeprobability`, `--opportunityratingcode` |
| `scripts/delete_opportunity.py` | Delete an opportunity | `--opportunity-id` | |

## Workflow: Resolving Names to GUIDs

When the user provides a **name** instead of a GUID:

**For account/contact names:**
1. Run `scripts/search_accounts.py --name "<keyword>"` or `scripts/search_contacts.py --name "<keyword>"`.
2. If exactly one result, use that GUID automatically.
3. If multiple results, ask the user to pick one.
4. If no results, inform the user and stop.

**For opportunity names (when `--opportunity-id` is needed):**
1. Run `scripts/list_opportunities.py --filter "contains(name, '<keyword>')"` to find matches.
2. If exactly one result, use its `id` automatically.
3. If multiple results, ask the user to pick one.
4. If no results, inform the user and stop.

Do NOT ask the user for a GUID if you can look it up by name.

## Field Mapping (CRM List View)

| Display Name | Script Parameter | Notes |
|-------------|-----------------|-------|
| Topic | `--name` | Required for create |
| Potential Customer | `--account-id` or `--contact-id` | GUID or name (auto-resolved) |
| Est. Close Date | `--estimatedclosedate` | Format: YYYY-MM-DD |
| Est. Revenue | `--estimatedvalue` | Numeric |
| Contact | `--parentcontactid` | GUID or name |
| Probability | `--closeprobability` | 0-100 |
| Rating | `--opportunityratingcode` | 1=Hot, 2=Warm, 3=Cold |

For detailed field reference and OData filter examples, see [references/FIELD_REFERENCE.md](references/FIELD_REFERENCE.md).

## Examples

**List all hot opportunities:**
```bash
python scripts/list_opportunities.py --filter "opportunityratingcode eq 1"
```

**Create a new deal:**
```bash
python scripts/create_opportunity.py --name "Enterprise License" --account-id "Fourth Coffee" --estimatedvalue 85000 --closeprobability 60
```

**Update probability:**
```bash
python scripts/update_opportunity.py --opportunity-id "<GUID>" --closeprobability 75
```

## Common Issues

### Authentication Failed
1. Verify `.env` has correct `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`.
2. Confirm the app registration has Dynamics CRM API permissions.
3. Ensure the app user is added to the Dataverse environment.

### No Results Returned
- Check OData filter syntax. Use `contains(name, 'keyword')` for partial matches.
- Verify `DATAVERSE_URL` points to the correct environment.
