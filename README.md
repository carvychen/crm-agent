# CRM Agent — Dynamics 365 Opportunity Management

An AI agent that manages Dynamics 365 CRM opportunities through natural language, built with [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) and Dataverse Web API.

The skill follows the [Agent Skills](https://agentskills.io) open standard — portable across Claude Code, GitHub Copilot, OpenAI Codex, Cursor, Gemini CLI, and any agent that supports SKILL.md discovery.

## Architecture

```
User (natural language)
  |
  v
agent.py — Agent (Azure AI Foundry LLM)
  |         + subprocess_script_runner
  |         + session memory / context compaction
  |         + error handling / retry / usage tracking middleware
  v
skills/crm-opportunity/ — File-based Skill (agentskills.io standard)
  |  SKILL.md            — instructions + frontmatter
  |  scripts/            — 7 CLI scripts (argparse + JSON output)
  |  references/         — field reference docs
  v
dataverse_client.py — Dataverse Web API
  |  Azure AD client credentials auth
  v
Dynamics 365 CRM
```

## Project Structure

```
crm-agent/
├── agent.py                          # Agent entry point (interactive CLI)
├── dataverse_client.py               # Dataverse Web API client (auth + CRUD + name resolution)
├── requirements.txt
├── .env.example
├── skills/
│   └── crm-opportunity/              # File-based skill (agentskills.io standard)
│       ├── SKILL.md                  # Skill instructions + YAML frontmatter
│       ├── scripts/                  # 7 CLI scripts (argparse → JSON)
│       │   ├── search_accounts.py
│       │   ├── search_contacts.py
│       │   ├── list_opportunities.py
│       │   ├── get_opportunity.py
│       │   ├── create_opportunity.py
│       │   ├── update_opportunity.py
│       │   └── delete_opportunity.py
│       └── references/
│           └── FIELD_REFERENCE.md    # OData field reference + filter examples
└── scripts/
    ├── example.py                    # Standalone CRUD demo (no agent)
    ├── discover_fields.py            # Explore Opportunity entity metadata
    └── cleanup.py                    # Delete test records by name
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
| File-based skill | `SkillsProvider(skill_paths=...)` — discovers `SKILL.md`, loads on demand |
| Subprocess execution | `subprocess_script_runner` — runs scripts as Python subprocesses with CLI args |
| Multi-turn memory | `agent.create_session()` — remembers context across turns |
| Context compaction | `SlidingWindowStrategy` — keeps last 20 message groups, prevents token overflow |
| Error recovery | Each script catches exceptions and returns structured JSON error |
| Name resolution | `resolve_account_id` / `resolve_contact_id` — auto-resolves names to GUIDs |
| Rate limit retry | `chat_middleware` + tenacity — exponential backoff, 3 attempts |
| Usage tracking | `chat_middleware` — logs token consumption per LLM call |
| Delete confirmation | `run_with_approval` — prompts user before destructive operations |

## Skill Scripts

All scripts are standalone CLI tools with `--help` support and JSON output.

| Script | Description | Required Args | Optional Args |
|--------|-------------|---------------|---------------|
| `search_accounts.py` | Find accounts by name | `--name` | |
| `search_contacts.py` | Find contacts by name | `--name` | |
| `list_opportunities.py` | Query with OData filters | | `--filter`, `--order-by`, `--top` |
| `get_opportunity.py` | Get one by GUID | `--opportunity-id` | |
| `create_opportunity.py` | Create new opportunity | `--name`, `--account-id` | `--contact-id`, `--estimatedvalue`, `--estimatedclosedate`, `--closeprobability`, `--opportunityratingcode` |
| `update_opportunity.py` | Partial update | `--opportunity-id` | `--name`, `--estimatedvalue`, `--estimatedclosedate`, `--closeprobability`, `--opportunityratingcode` |
| `delete_opportunity.py` | Delete by GUID | `--opportunity-id` | |

## Cross-Platform Compatibility

The skill follows the [Agent Skills](https://agentskills.io) open standard and can be used with any compatible agent:

| Platform | How to Use |
|----------|-----------|
| **Agent Framework** | `SkillsProvider(skill_paths="skills/", script_runner=subprocess_script_runner)` |
| **Claude Code** | Place `skills/crm-opportunity/` in your project; Claude discovers `SKILL.md` automatically |
| **GitHub Copilot** | Same — Copilot reads `SKILL.md` from the workspace |
| **OpenAI Codex** | Same — Codex discovers skills via `SKILL.md` |
| **Cursor / Gemini CLI** | Same — any agent supporting agentskills.io standard |

Scripts can also be run standalone:

```bash
# Search accounts
python skills/crm-opportunity/scripts/search_accounts.py --name "Fourth Coffee"

# List hot opportunities
python skills/crm-opportunity/scripts/list_opportunities.py --filter "opportunityratingcode eq 1"
```

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
