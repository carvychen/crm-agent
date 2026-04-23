---
name: crm-opportunity
description: Manage Dynamics 365 CRM opportunities (list, search, get, create, update, delete) through the crm-agent MCP server. Use when user mentions CRM opportunities, sales pipeline, deals, revenue, or says things like "list my opportunities", "create a deal", "update that deal", "delete it".
license: MIT
compatibility: Any MCP-compliant client (Claude Desktop, VS Code Copilot MCP, Copilot Studio, or a custom-built agent). No Python, no credentials, no runtime dependencies inside this bundle.
metadata:
  version: "2.0"
  transport: Streamable HTTP (MCP SDK standard)
---

# CRM Opportunity Skill

This skill tells an MCP-aware agent **how and when to use the Dynamics 365 CRM tools** exposed by the crm-agent MCP server. Every tool is implemented in the MCP server (see the main repo's `src/mcp_server.py`); this bundle carries only the SOP + the endpoint pointer — it contains no runtime code, no credentials, and no cloud-specific hostnames.

Think of it as "the instruction manual that ships with the tools". A customer can drop this folder into any MCP-compliant agent host, edit `.mcp.json` to point at their deployed MCP server, and the agent knows what tools exist and when to use them.

## Setup (one time per agent host)

1. Have a running crm-agent MCP server. For Lenovo that means the Bicep-deployed Function App's `/mcp` endpoint; for local development, `func start` on `http://localhost:7071/mcp`.
2. Open [`.mcp.json`](./.mcp.json) in this folder and replace `REPLACE-WITH-YOUR-FUNCTION-APP-HOST` with your deployment's hostname.
3. Wire the `.mcp.json` into your agent host's configuration — the exact mechanism differs per host:
   - **Claude Desktop** → Settings → Developer → Edit Config, copy the `mcpServers.crm` block in.
   - **VS Code / GitHub Copilot MCP** → `.vscode/mcp.json`, same shape.
   - **Custom agent** → pass `mcpServers["crm"].url` to your MCP client (e.g. `agent_framework.MCPStreamableHTTPTool(url=...)`).

Every inbound tool call must carry `Authorization: Bearer <user-jwt>` where the JWT's audience matches the MCP server's AAD app Application ID URI. How the host obtains that JWT is host-specific and out of scope for this skill.

## Available tools

The MCP server advertises these tools via `list_tools`. Trust that advertisement over anything written here — the tool schemas are the authoritative contract.

| Tool | Purpose | Required | Notes |
|---|---|---|---|
| `list_opportunities` | List opportunities visible to the caller | — | Supports OData `$filter`, `$top`, `$orderby` |
| `get_opportunity` | Fetch one opportunity by GUID | `opportunity_id` | |
| `search_accounts` | Resolve an account name to its GUID | `query` | Returns `[{id, name}, ...]` |
| `search_contacts` | Resolve a contact name to its GUID | `query` | Same shape as `search_accounts` |
| `create_opportunity` | Create a new opportunity | `name`, `customer_id`, `customer_type` | `customer_type` is `account` or `contact` (polymorphic `customerid`) |
| `update_opportunity` | Partial update | `opportunity_id` | Only fields you supply are changed |
| `delete_opportunity` | Permanently delete | `opportunity_id` | Destructive — the reference agent gates this behind user approval |

Rating values: `1`=Hot, `2`=Warm, `3`=Cold. Dates: `YYYY-MM-DD`. See [`references/FIELD_REFERENCE.md`](./references/FIELD_REFERENCE.md) for the full OData filter cookbook.

## SOP — how the agent should behave

### 1. Resolve names before writing

If the user names an account or contact (e.g. "create a deal for Fourth Coffee"), **always** resolve it first:

1. Call `search_accounts(query="Fourth Coffee")` (or `search_contacts`).
2. If one match, use that GUID silently.
3. If >1 match, present the candidate list and ask the user to pick. Do **not** default to the first result.
4. If 0 match, tell the user "no account found matching X" and stop. Do **not** fabricate a GUID or proceed to `create_opportunity`.

### 2. Confirm destructive operations

- `delete_opportunity` — always describe what you are about to delete (the topic name, the customer name, the revenue) **before** calling the tool. The reference agent layers MCP approval on top; external clients should do the same.
- `update_opportunity` with large swings (probability drop ≥ 30 points, revenue change ≥ 50%) — briefly state what you're about to change and why.

### 3. Respect Dataverse RLS

Every call runs under the **real user's** identity (OBO). That means the tool may return fewer records than "all opportunities in Dataverse" — the RLS filter is doing its job. Do not attempt to work around it; if the user should see more, the fix is in Dataverse security roles, not the agent.

### 4. Report errors faithfully

When a tool returns an error, read the message to the user unmodified; don't paraphrase it into "sorry, something went wrong". Dataverse error messages are diagnostic and the operator / sales rep can often fix the problem themselves given the concrete text.

## Example dialogues

**Single-match resolution + create**

```
User:   Create a 60k deal for Fourth Coffee closing in July
Agent:  [search_accounts(query="Fourth Coffee")] → one match
        [create_opportunity(name="Fourth Coffee deal", customer_id=<guid>,
                            customer_type="account", estimated_value=60000,
                            estimated_close_date="2026-07-31")]
        Done. Created "Fourth Coffee deal" for 60k, closing 2026-07-31.
```

**Disambiguation prompt**

```
User:   Show me the Lenovo opportunity
Agent:  [search_accounts(query="Lenovo")] → 3 matches
        I see three accounts with "Lenovo" in the name:
          1. Lenovo Group Ltd
          2. Lenovo Global
          3. Lenovo (HK) Trading
        Which one did you mean?
```

**Destructive confirmation**

```
User:   Delete the deal we just made
Agent:  I'm about to delete "Fourth Coffee deal" (60,000 USD, closing 2026-07-31).
        This cannot be undone. Please confirm.
User:   Confirmed
Agent:  [delete_opportunity(opportunity_id=<guid>)]
        Deleted.
```

## Compatibility

This bundle works with any client that speaks [Streamable HTTP MCP](https://modelcontextprotocol.io/). Tested against:

- Claude Desktop
- VS Code / GitHub Copilot MCP
- The reference agent in this repo (`src/agent/`)
- Copilot Studio (custom connector path)

Host-specific wiring instructions are in each host's own documentation; the skill's only interface with the host is `.mcp.json`.

## Further reading

- [`references/FIELD_REFERENCE.md`](./references/FIELD_REFERENCE.md) — field-by-field mapping + OData filter cookbook
- Main repo `docs/` — project architecture, ADRs, deployment runbooks
