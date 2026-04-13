# Opportunity Field Reference

## Writable Fields

| Parameter | API Field | Type | Notes |
|-----------|-----------|------|-------|
| name | name | string | Required. The opportunity title (Topic). |
| account_id | customerid_account@odata.bind | GUID or name | Potential Customer (account). Auto-resolved if name given. |
| contact_id | customerid_contact@odata.bind | GUID or name | Potential Customer (contact). Auto-resolved if name given. |
| estimatedclosedate | estimatedclosedate | date | Format: YYYY-MM-DD |
| estimatedvalue | estimatedvalue | float | Expected deal value |
| closeprobability | closeprobability | int | Win probability 0-100 |
| opportunityratingcode | opportunityratingcode | int | 1=Hot, 2=Warm, 3=Cold |
| parentcontactid | parentcontactid@odata.bind | GUID or name | Related contact person |

## Read-Only Fields (returned in responses)

| Field | Description |
|-------|-------------|
| id / opportunityid | Unique GUID |
| topic / name | Opportunity title |
| potential_customer | Formatted customer name |
| est_close_date | Expected close date |
| est_revenue | Expected deal value |
| contact | Related contact name |
| account | Related account name |
| probability | Win probability |
| rating | Hot / Warm / Cold |

## Rating Values

| Code | Label |
|------|-------|
| 1 | Hot |
| 2 | Warm |
| 3 | Cold |

## OData $filter Examples

```
# All opportunities (no filter)
(leave --filter empty or omit)

# Revenue greater than 50,000
estimatedvalue gt 50000

# Hot rating only
opportunityratingcode eq 1

# Name search (partial match)
contains(name, 'keyword')

# Closing before a specific date
estimatedclosedate lt 2026-12-31

# Combined filters
estimatedvalue gt 20000 and opportunityratingcode eq 1

# Closing within a date range
estimatedclosedate ge 2026-01-01 and estimatedclosedate le 2026-06-30
```

## OData $orderby Examples

```
# By revenue descending
estimatedvalue desc

# By close date ascending
estimatedclosedate asc

# By name
name asc
```

## Customer Field (Polymorphic)

The "Potential Customer" field is polymorphic — it can reference either an account or a contact:
- Use `customerid_account@odata.bind` with value `/accounts({GUID})` for accounts
- Use `customerid_contact@odata.bind` with value `/contacts({GUID})` for contacts
- Only one should be set per opportunity
