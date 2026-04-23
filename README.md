# CRM Agent Platform — Reference Implementation

Four independent deliverables for managing Dynamics 365 opportunities at Lenovo:

- **MCP server** — Azure Functions HTTP endpoint exposing CRM tools via Model Context Protocol. Consumable by any MCP-compliant client.
- **Reference agent** — production-grade LLM + tool-calling runtime; calls the MCP server over HTTP like any external agent.
- **Prompt module** — the reference agent's behaviour prompts as Markdown files.
- **Skill bundle** — agent-neutral SOP + `.mcp.json` pointer that any MCP-aware agent can consume.

See `docs/CONTEXT.md` for the full glossary and project invariants, `docs/adr/` for architectural decisions, and `PRD issue #2` for the full roadmap.

## Current state

The repo is mid-refactor: the legacy monolithic demo (`agent.py` + `skills/crm-opportunity/`) still runs unchanged, while the new layered products land slice by slice (tracked in GitHub issues #3–#12).

**Slice 1 (#3) — MCP server walking skeleton** (merged)

- `src/config.py`, `src/auth.py`, `src/dataverse_client.py`, `src/mcp_server.py`, `src/asgi.py` — cloud-neutral config, OBO-over-WIF, OData client, MCP Server + Streamable HTTP
- `function_app.py` — Azure Functions v2 entry
- 12 pytest cases + end-to-end HTTP integration test with mocked Dataverse

**Slice 2 (#4) — Reference agent walking skeleton** (this PR, ADR 0005 amended)

- `src/agent/prompts/` — Markdown prompt module (`system.zh.md`, `safety_rules.md`, `{current_date}` substitution via `PromptLoader` — ADR 0006)
- `src/agent/builder.py` — composes `agent_framework.Agent` with `FoundryChatClient` + `MCPStreamableHTTPTool`; the AF tool's `header_provider` reads a per-request `current_user_jwt` ContextVar
- `src/agent/route.py` — `POST /api/chat` streams AF `AgentResponseUpdate`s as OpenAI-compatible SSE chunks + `[DONE]`
- `src/asgi.py` — `create_asgi_app(deps, *, agent=None)` mounts `/api/chat` only when `ENABLE_REFERENCE_AGENT=true`
- 10 new pytest cases covering prompt loader, builder wiring, chat-route SSE, agent-disabled path, and the ContextVar / header-provider isolation pattern

## Prerequisites (new stack)

- Python 3.11 (pinned; see `.python-version`)
- `mamba`/`conda` or `pyenv` to source a 3.11 interpreter
- For live runs only: Azure CLI logged in (`az login`), an AAD app with Federated Identity Credential, and a Dataverse application user

## Local development

```bash
rm -rf .venv && mamba create --prefix ./.venv python=3.11 -y  # or pyenv/venv equivalent
.venv/bin/pip install -r requirements-dev.txt                 # prod + test deps
.venv/bin/pytest                                              # 12 tests, <1s
```

The test suite runs without any Azure resources — OBO exchanges and Dataverse calls are mocked via `respx` and `httpx.MockTransport`. For real-tenant smoke testing, wait for the pre-flight script that lands in Slice 8 (#10).

## Environment variables (MCP server)

All cloud-specific values are driven by `CLOUD_ENV` so that shipping to Azure China is a parameter flip (ADR 0003). Slice 1 only exercises the `global` branch.

| Variable | Required | Description |
|---|---|---|
| `CLOUD_ENV` | No (default `global`) | Selects cloud-specific endpoints/authority/FIC audience |
| `DATAVERSE_URL` | Yes | e.g. `https://org7339c4fb.crm.dynamics.com` |
| `AAD_APP_CLIENT_ID` | Yes | AAD app registration client ID (OBO target) |
| `AAD_APP_TENANT_ID` | Yes | Tenant ID of the AAD app |
| `MANAGED_IDENTITY_CLIENT_ID` | No | Specify when multiple MIs are attached |

## Environment variables (reference agent)

| Variable | Required | Description |
|---|---|---|
| `ENABLE_REFERENCE_AGENT` | No (default `true`) | `false` skips the `/api/chat` route entirely |
| `LLM_PROVIDER` | No (default `foundry`) | Slice 2 ships `foundry` only; others land in #8 |
| `FOUNDRY_PROJECT_ENDPOINT` | Yes (if agent enabled) | Foundry project base URL |
| `FOUNDRY_MODEL` | No (default `gpt-4o-mini`) | Deployment name |
| `MCP_SERVER_URL` | Yes (if agent enabled) | URL the agent posts to; typically the app's own `/mcp` endpoint |

See `.env.example` for the full template.

## Legacy demo (unchanged until later slices)

The original Microsoft Agent Framework demo still lives at the repo root:

```bash
.venv/bin/pip install -r requirements.txt agent-framework==1.0.1   # extra dep not in the slim Slice 1 pin
python agent.py
```

| Variable | Description |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint |
| `FOUNDRY_MODEL` | Model deployment (default `gpt-4o-mini`) |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | Dataverse creds (client-secret flow — to be eliminated by Slice 7) |

The skill bundle at `skills/crm-opportunity/` is rewritten in Slice 7 (#9) to drop credentials and Python scripts in favour of the MCP server.
