# CRM Agent — Dynamics 365 Opportunity Management

An AI agent that manages Dynamics 365 CRM opportunities through natural language, built with [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) and Dataverse Web API.

## Architecture

```
User (natural language)
  |
  v
agent.py — Agent (Azure AI Foundry LLM)
  |         + session memory
  |         + context compaction
  |         + error handling / retry / usage tracking middleware
  v
crm_skill.py — Skill (7 scripts)
  |  search_accounts / search_contacts
  |  list / get / create / update / delete opportunities
  v
dataverse_client.py — Dataverse Web API
  |  Azure AD client credentials auth
  v
Dynamics 365 CRM
```

## Project Structure

```
crm-api/
├── agent.py              # Agent entry point (interactive CLI)
├── crm_skill.py          # Agent skill definition (7 scripts)
├── dataverse_client.py   # Dataverse Web API client (auth + CRUD)
├── requirements.txt
├── .env.example
└── scripts/
    ├── example.py        # Standalone CRUD demo (no agent)
    ├── discover_fields.py# Explore Opportunity entity metadata
    └── cleanup.py        # Delete test records by name
```

## Prerequisites

- Python 3.10+
- Azure Entra ID app registration with:
  - Client ID, Tenant ID, Client Secret
  - API permission for Dynamics CRM (`user_impersonation` or application permission)
  - App user added to Dataverse environment
- Azure AI Foundry project with a deployed model (e.g. `gpt-4o-mini`)
- Azure CLI logged in (`az login`)

## Setup

```bash
# 1. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — fill in AZURE_CLIENT_SECRET and FOUNDRY_PROJECT_ENDPOINT
```

### .env variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_TENANT_ID` | Yes | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | Yes | App registration client ID |
| `AZURE_CLIENT_SECRET` | Yes | App registration client secret |
| `DATAVERSE_URL` | Yes | e.g. `https://org7339c4fb.crm.dynamics.com` |
| `FOUNDRY_PROJECT_ENDPOINT` | Yes | Azure AI Foundry project endpoint |
| `FOUNDRY_MODEL` | No | Model deployment name (default: `gpt-4o-mini`) |

## Usage

### Start the agent

```bash
python agent.py
```

Then chat naturally:

```
You: list all hot opportunities
You: create a deal called "Enterprise License", account Fourth Coffee, revenue 85000
You: change that deal's probability to 75%
You: delete it
```

The agent will:
- Automatically search for accounts/contacts by name (no need to provide GUIDs)
- Remember conversation context across turns
- Confirm before destructive operations

### Run scripts directly (no agent)

```bash
# CRUD demo
python scripts/example.py

# Explore all Opportunity fields
python scripts/discover_fields.py

# Clean up test records
python scripts/cleanup.py
```

## Agent Capabilities

| Feature | Implementation |
|---------|---------------|
| Multi-turn memory | `agent.create_session()` — remembers context across turns |
| Context compaction | `SlidingWindowStrategy` — keeps last 20 message groups, prevents token overflow |
| Error recovery | `function_middleware` — catches Dataverse API errors, returns them to LLM for reasoning |
| Rate limit retry | `chat_middleware` + tenacity — exponential backoff, 3 attempts |
| Usage tracking | `chat_middleware` — logs token consumption per LLM call |
| Delete confirmation | `run_with_approval` — prompts user before destructive operations |

## Skill Scripts

| Script | Description | Required Parameters |
|--------|-------------|---------------------|
| `search_accounts` | Find accounts by name | `name` |
| `search_contacts` | Find contacts by name | `name` |
| `list_opportunities` | Query with OData filters | _(optional: filter, order_by, top)_ |
| `get_opportunity` | Get one by GUID | `opportunity_id` |
| `create_opportunity` | Create new opportunity | `name`, `account_id` |
| `update_opportunity` | Partial update | `opportunity_id` |
| `delete_opportunity` | Delete by GUID | `opportunity_id` |

## Opportunity Fields

Fields matching the CRM list view:

| Display Name | API Field | Type | Notes |
|-------------|-----------|------|-------|
| Topic | `name` | string | Required |
| Potential Customer | `customerid_account@odata.bind` | GUID | Required (polymorphic — use `_account` or `_contact` suffix) |
| Est. Close Date | `estimatedclosedate` | date | `YYYY-MM-DD` |
| Est. Revenue | `estimatedvalue` | float | |
| Contact | `parentcontactid@odata.bind` | GUID | Write via `@odata.bind`, read via `_parentcontactid_value` |
| Account | `parentaccountid@odata.bind` | GUID | Write via `@odata.bind`, read via `_parentaccountid_value` |
| Probability | `closeprobability` | int | 0–100 |
| Rating | `opportunityratingcode` | int | 1=Hot, 2=Warm, 3=Cold |

## OData Filter Examples

```python
# All opportunities (no filter)
client.list()

# Revenue > 50,000
client.list(filter_expr="estimatedvalue gt 50000")

# Hot rating
client.list(filter_expr="opportunityratingcode eq 1")

# Name search
client.list(filter_expr="contains(name, 'Enterprise')")

# Closing before a date
client.list(filter_expr="estimatedclosedate lt 2026-12-31")

# Combined
client.list(filter_expr="estimatedvalue gt 20000 and opportunityratingcode eq 1")
```
