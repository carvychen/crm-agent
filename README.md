# CRM Agent Platform — Reference Implementation

Four independent deliverables for managing Dynamics 365 opportunities at Lenovo:

- **MCP server** — Azure Functions HTTP endpoint exposing CRM tools via Model Context Protocol. Consumable by any MCP-compliant client.
- **Reference agent** — production-grade LLM + tool-calling runtime; calls the MCP server over HTTP like any external agent.
- **Prompt module** — the reference agent's behaviour prompts as Markdown files.
- **Skill bundle** — agent-neutral SOP + `.mcp.json` pointer that any MCP-aware agent can consume.

See `docs/CONTEXT.md` for the full glossary and project invariants, `docs/adr/` for architectural decisions, and `PRD issue #2` for the full roadmap.

## Documentation map

### For the person deploying (one-time)

1. [docs/deployment/aad-setup.md](./docs/deployment/aad-setup.md) — identity admin: AAD app + FIC
2. [docs/deployment/dataverse-setup.md](./docs/deployment/dataverse-setup.md) — D365 admin: application user + role
3. [docs/deployment/bicep-deploy.md](./docs/deployment/bicep-deploy.md) — platform engineer: Bicep deploy + code zip
4. [docs/deployment/preflight.md](./docs/deployment/preflight.md) — anyone: `scripts/preflight.py` validates the chain end-to-end

### For the person operating (ongoing)

- [docs/operations/troubleshooting.md](./docs/operations/troubleshooting.md) — symptom → cause → diagnostic → remediation
- [docs/operations/monitoring.md](./docs/operations/monitoring.md) — Bicep-deployed alerts + KQL queries for investigation
- [docs/operations/secret-rotation.md](./docs/operations/secret-rotation.md) — FIC / MI / role / Foundry SP rotation (WIF means "almost nothing to rotate")

### For the person extending the codebase

- [docs/CONTEXT.md](./docs/CONTEXT.md) — invariants + glossary
- [docs/adr/](./docs/adr/) — architectural decisions; read 0001–0008 in order
- [infra/README.md](./infra/README.md) — Bicep layout + post-deploy checklist

## Current state

The repo lands capability slice by slice (tracked in GitHub issues #3–#12). The legacy monolithic demo (`agent.py` + `skills/crm-opportunity/`) still runs unchanged alongside the layered reference implementation.

Merged to `main`:

- **Slice 1 (#3) — MCP server walking skeleton.** `src/config.py`, `src/auth.py`, `src/dataverse_client.py`, `src/mcp_server.py`, `src/asgi.py` — cloud-neutral config, OBO-over-WIF, OData client, MCP Server + Streamable HTTP. Azure Functions v2 entry in `function_app.py`.
- **Slice 2 (#4) — Reference agent walking skeleton.** `src/agent/{prompts,builder,route}.py` — `agent_framework.Agent` + `FoundryChatClient` + `MCPStreamableHTTPTool`, `POST /api/chat` as SSE, `ContextVar`-scoped user JWT propagated into the MCP tool via `header_provider`. Gated by `ENABLE_REFERENCE_AGENT`.
- **Slices 3–5 — hardening, multi-cloud parity, runbooks.** Config fail-loud, `CLOUD_ENV={global,china}` wiring (ADR 0003), deployment runbooks under `docs/deployment/`.
- **Slice 6 (#23) — LLM provider dispatch.** `LLM_PROVIDER={foundry,azure-openai-global,azure-openai-cn,custom}` with a per-provider prompt module.
- **Slice 7 (#22) — skill bundle rewrite.** `skills/crm-opportunity/` is now an MCP consumer (framework-neutral), not a credentialed script.
- **Slice 10 (#21) — runbooks.** Four operator-facing guides (`aad-setup`, `dataverse-setup`, `bicep-deploy`, `preflight`) and the troubleshooting table under `docs/operations/`.
- **Slice 12 (#26) — Flex Consumption + identity-based storage (ADR 0008).** Removes the last long-lived secret (`AzureWebJobsStorage` shared-key connection string) in favour of UAMI + data-role RBAC; works around the `azure-functions` SDK leading-slash routing bug via `src/flex_asgi.FlexAsgiFunctionApp`.

In flight in this branch:

- **Slice 11 (#24) — delivery rehearsal (this PR).** A second-operator dry-run of the whole Slice 5+ runbook on a fresh Azure tenant. Log + findings: [`docs/deployment/rehearsal-global.md`](./docs/deployment/rehearsal-global.md). Seven runbook bugs found and fixed; two deploy-auth bugs (UAMI client-id resolution on Flex, OBO error surfacing) fixed in code. AC3 proof: full teardown + rebuild in ~7 min wall-clock on the patched runbook. AC2 intentionally scoped: cross-tenant FIC is not supported (ADR 0001 — this is a same-tenant architectural pattern); the rehearsal hit that boundary at `AADSTS700236` and confirmed it is an Entra policy, not code or config.

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

The unit suite runs without any Azure resources — OBO exchanges and Dataverse calls are mocked via `respx` and `httpx.MockTransport`. Live-integration tests (`tests/integration/`) read credentials from a gitignored repo-root `.env` (see `.env.example` for the full template) and hit real Entra + Dataverse + Foundry on every PR per [ADR 0007](./docs/adr/0007-testing-discipline.md).

## Environment variables (MCP server)

All cloud-specific values are driven by `CLOUD_ENV` so that shipping to Azure China is a parameter flip (ADR 0003). Since Slice 5 both `global` (Azure Public) and `china` (Azure 21Vianet) are supported.

| Variable | Required | Description |
|---|---|---|
| `CLOUD_ENV` | No (default `global`) | `global` or `china`; unknown values fail-loud at boot |
| `DATAVERSE_URL` | Yes | e.g. `https://org7339c4fb.crm.dynamics.com` (Global) or `.../.crm.dynamics.cn` (China) |
| `AAD_APP_CLIENT_ID` | Yes | AAD app registration client ID (OBO target) |
| `AAD_APP_TENANT_ID` | Yes | Tenant ID of the AAD app |
| `MANAGED_IDENTITY_CLIENT_ID` | No | Specify when multiple MIs are attached |

## Environment variables (reference agent)

| Variable | Required | Description |
|---|---|---|
| `ENABLE_REFERENCE_AGENT` | No (default `true`) | `false` skips the `/api/chat` route entirely |
| `LLM_PROVIDER` | No (default `foundry`) | `foundry` / `azure-openai-global` / `azure-openai-cn` / `custom` |
| `FOUNDRY_PROJECT_ENDPOINT` | If `LLM_PROVIDER=foundry` | Foundry project base URL |
| `FOUNDRY_MODEL` | No (default `gpt-4o-mini`) | Deployment name |
| `AZURE_OPENAI_ENDPOINT` | If `LLM_PROVIDER=azure-openai-*` | Azure OpenAI endpoint; `.com` on Global, `.cn` on 21Vianet |
| `AZURE_OPENAI_API_VERSION` | No (default `2024-10-21`) | |
| `CUSTOM_LLM_CLIENT_FACTORY` | If `LLM_PROVIDER=custom` | Dotted path to a zero-arg factory that returns a `SupportsChatGetResponse` |
| `MCP_SERVER_URL` | Yes (if agent enabled) | URL the agent posts to; typically the app's own `/mcp` endpoint |

See `.env.example` for the full template.

### Adding a new LLM provider

The `LLM_PROVIDER=custom` path is a stable extension point. A customer integrates a model we never shipped support for by:

1. Implementing an object with an `async def get_response(messages, *, stream=False, **kwargs)` method (matches AF's `SupportsChatGetResponse` protocol).
2. Packaging it in an importable Python module.
3. Setting `CUSTOM_LLM_CLIENT_FACTORY=your.module.path:factory_callable` where `factory_callable` takes no args and returns an instance.

Minimal example:

```python
# your_org/llm/qwen.py
class QwenChatClient:
    async def get_response(self, messages, *, stream=False, **kwargs):
        ...  # your implementation

def make_qwen():
    return QwenChatClient()
```

Then: `LLM_PROVIDER=custom` + `CUSTOM_LLM_CLIENT_FACTORY=your_org.llm.qwen:make_qwen`.

Prompts that need to change per provider go under `src/agent/prompts/providers/{provider}.md` — the `PromptLoader` appends them to the base system prompt automatically when the matching provider is active (ADR 0006 / ADR 0005).

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
