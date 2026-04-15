# CRM Agent

An AI agent that manages Dynamics 365 CRM opportunities through natural language, built with [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) and Dataverse Web API.

The skill follows the [Agent Skills](https://agentskills.io) open standard — portable across Claude Code, GitHub Copilot, OpenAI Codex, Cursor, Gemini CLI, and any agent that supports SKILL.md discovery.

## How It Works

```
User (natural language)
  → Agent (Azure AI Foundry LLM + session memory + middleware)
    → File-based Skill (agentskills.io standard, 7 CLI scripts)
      → Dataverse Web API
        → Dynamics 365 CRM
```

The agent uses `subprocess_script_runner` to execute standalone Python scripts as tools. Each script handles one CRM operation (search, list, create, update, delete) with argparse CLI args and JSON output. Name-to-GUID resolution is automatic — users never need to provide raw IDs.

## Prerequisites

- Python 3.10+
- Azure CLI logged in (`az login`)
- Azure Entra ID app registration with Dynamics CRM API permission and an app user in the Dataverse environment
- Azure AI Foundry project with a deployed model (e.g. `gpt-4o-mini`)

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                       # Foundry endpoint
cp skills/crm-opportunity/.env.example skills/crm-opportunity/.env  # Dataverse credentials
# Edit both .env files — fill in the required values
```

### Environment Variables

项目有两层 `.env`，分别管各自的凭据：

**根目录 `.env`** — Agent 大模型配置：

| Variable | Required | Description |
|----------|----------|-------------|
| `FOUNDRY_PROJECT_ENDPOINT` | Yes | Azure AI Foundry project endpoint |
| `FOUNDRY_MODEL` | No | Model deployment name (default: `gpt-4o-mini`) |

**`skills/crm-opportunity/.env`** — Dataverse 连接凭据：

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_TENANT_ID` | Yes | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | Yes | App registration client ID |
| `AZURE_CLIENT_SECRET` | Yes | App registration client secret |
| `DATAVERSE_URL` | Yes | e.g. `https://org7339c4fb.crm.dynamics.com` |

## Usage

```bash
python agent.py
```

```
You: list all hot opportunities
You: create a deal called "Enterprise License", account Fourth Coffee, revenue 85000
You: change that deal's probability to 75%
You: delete it
```

The agent remembers context across turns, resolves names to GUIDs automatically, and asks for confirmation before destructive operations.

Utility scripts are also available for development and debugging:

```bash
python scripts/example.py         # CRUD demo
python scripts/discover_fields.py # Explore Opportunity entity fields
python scripts/cleanup.py         # Remove test records
```

## Cross-Platform Compatibility

The skill under `skills/crm-opportunity/` follows the agentskills.io standard. Any compatible agent (Claude Code, GitHub Copilot, Codex, Cursor, Gemini CLI) will auto-discover `SKILL.md` from the workspace. Scripts can also be run standalone:

```bash
python skills/crm-opportunity/scripts/search_accounts.py --name "Fourth Coffee"
python skills/crm-opportunity/scripts/list_opportunities.py --filter "opportunityratingcode eq 1"
```

See `skills/crm-opportunity/SKILL.md` for full script reference, field mappings, and OData filter examples.
