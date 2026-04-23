# CRM Agent Platform — Reference Implementation

Three independent, production-grade deliverables for managing Dynamics 365 opportunities at Lenovo: an **MCP server** exposing CRM tools, a **reference agent** that orchestrates LLM + tool calls over them, and a **skill bundle** that tells any agent how to use them. Each stands alone; they compose by choice, not by coupling.

## Project origin

A working demo already exists: a Microsoft Agent Framework agent that injects a `skill/` folder in which prompt knowledge, Dataverse calling logic, and a `.env` with sensitive credentials are all **entangled in a single bundle**. This repo's purpose is to **disentangle** that bundle into layered products where secrets, tool implementation, and prompt knowledge no longer co-locate, and where each layer can evolve independently.

## Project invariants

These rules hold across every decision, PR, and ADR. If a proposed change conflicts with an invariant, the invariant wins.

1. **Layers are independently deployable and evolvable.** The four layers — **skill bundle**, **MCP server**, **agent**, and any future **orchestrator** — communicate only through documented contracts (MCP protocol, HTTP endpoints, `.mcp.json`). No layer may reach into another's internals, and no layer may hide a dependency on a sibling.
   - The **MCP server** works for any MCP-compliant client — reference agent, VS Code, Claude Desktop, Copilot Studio, a customer-built agent or orchestrator.
   - The **skill bundle** carries SOP + `.mcp.json` only and works with any MCP-compliant agent.
   - The **reference agent** talks to the MCP server over the same HTTP + MCP protocol an external agent would use (see [ADR 0004](./adr/0004-http-mcp-transport-for-reference-agent.md)).
   - A future **multi-agent orchestrator** MUST compose agents through these same contracts rather than coupling them.
2. **The reference agent is production-grade.** It is a complete agent runtime, not a demo or stub — if Lenovo chooses to run it in production, it must meet the same quality bar as the MCP server. Its optionality is at *deployment time*, not in *build quality*.
3. **Code is cloud-neutral.** All cloud-specific endpoints, authorities, audiences, and LLM providers are configured via environment variables. No `dynamics.com` / `dynamics.cn` (or equivalent) literals in source.
4. **Delivered blind.** The project assumes its authors have no access to the customer's target environment. Every failure mode must be diagnosable from the shipped runbook, error messages, and pre-flight script.

## Language

### Deliverables

**MCP server**:
The HTTP endpoint exposing opportunity/account/contact tools via Model Context Protocol. Independent deliverable — consumable by any MCP client.
_Avoid_: "API", "backend", "tool server".

**Reference agent** (or just "agent"):
The agent runtime shipped in this repo. Receives user chat turns, calls an LLM, routes tool calls to the MCP server, and returns the response. Its behaviour prompts live in the **prompt module** (`src/agent/prompts/`) as Markdown files, not in Python. Independent deliverable — production-quality agent that Lenovo may run as-is, or replace with their own.
_Avoid_: "bot", "chatbot", "orchestrator" (see ambiguity resolution below).

**Prompt module**:
The Markdown files under `src/agent/prompts/` carrying the reference agent's system prompt, safety rules, few-shot examples, and per-provider variants. File-based to enable non-engineer editing, per-provider swapping, and independent review tracks. Scoped inside the reference agent — tightly coupled to its orchestration loop, so NOT a cross-cutting deliverable. See [ADR 0006](./adr/0006-prompt-as-file-module.md).

**Skill bundle** (or "skill"):
The file-based bundle (`SKILL.md` + `.mcp.json`) that tells any agent how and when to use the MCP server. Independent deliverable — works with any MCP-compliant agent.

**External agent**:
Any MCP-compliant client *other than* the reference agent — VS Code, Claude Desktop, Copilot Studio, a customer-built agent, etc. Treated identically to the reference agent by the MCP server.

**Orchestrator** (anticipated, not in current scope):
A layer above individual agents that routes a user request across multiple agents and composes their responses — e.g. a CRM agent + a calendar agent + an email agent working together. Out of scope for this project, but the decoupling invariant anticipates it: the MCP server and skill bundle must remain usable by a future orchestrator without modification.

**UI**:
The chat surface used by salespeople (web frontend, Teams bot, etc.). Out of scope for this repo; the reference agent exposes an HTTP endpoint any UI can call.

### Domain terms (Dynamics 365)

**Opportunity**:
A deal record in Dynamics 365 representing a potential sale. The primary entity this project manipulates.
_Avoid_: "deal", "revenue opportunity".

**Potential Customer**:
The Account or Contact that the Opportunity is for. Written via the polymorphic `customerid` field.
_Avoid_: "client", "buyer".

**RLS (Row-Level Security)**:
Dataverse's built-in per-user access control. Different sales reps see different opportunities based on ownership, team membership, or business unit.

### Identity & auth

**OBO (On-Behalf-Of)**:
OAuth 2.0 flow in which the MCP server exchanges a user's token for a Dataverse-scoped token, preserving user identity end-to-end.
_Avoid_: "delegated auth" (too generic).

**WIF (Workload Identity Federation) / FIC (Federated Identity Credential)**:
Mechanism that lets an AAD application trust a Managed Identity as a client credential, eliminating long-lived client secrets.

**Impersonation**:
Dataverse-specific pattern where a service account acts as another user via the `CallerObjectId` header. Considered and rejected — see [ADR 0001](./adr/0001-obo-with-wif.md).

**CLOUD_ENV**:
Environment variable with values `global` or `china` that selects the set of cloud-specific endpoints, authorities, and FIC audiences at runtime.

**LLM_PROVIDER**:
Environment variable selecting the LLM backend (e.g. `azure-openai-global`, `azure-openai-cn`, `foundry`, `custom`). Decouples agent code from model provider. Per-provider prompt variations live in the **prompt module** (`prompts/providers/*.md`), not in provider classes.

### Delivery terms

**Landing zone**:
Lenovo's pre-configured Azure China tenant with platform policies, shared services, and module-based IaC. Deployment target for production.

**Pre-flight**:
The validation script (`scripts/preflight.py`) the customer runs *before* deployment to verify network reachability, AAD configuration, and Dataverse access.

## Relationships

- A **User** authenticates against AAD; the **reference agent** or any **external agent** calls the **MCP server** on their behalf.
- The **MCP server** performs **OBO** using **WIF** to obtain a Dataverse-scoped token; **RLS** in **Dataverse** then filters **Opportunities** to what the **User** is allowed to see.
- The **skill bundle** carries business SOP and a `.mcp.json` pointer; any MCP-compliant agent — reference or external — can use it to drive the **MCP server**.
- The **reference agent** calls the **MCP server** over HTTP even when they share a Function App, so both paths exercise the same transport — see [ADR 0004](./adr/0004-http-mcp-transport-for-reference-agent.md).
- **CLOUD_ENV** is set once per deployment; it parameterises every endpoint, authority, and audience the **MCP server** and **reference agent** use.

## Example dialogue

> **Dev:** "When the agent calls `list_opportunities`, does the user see all their team's opportunities?"
>
> **Domain expert:** "Yes — because we use OBO, the Dataverse call runs as the real user, and Dataverse RLS applies. If the user is in a team with team-scoped access, they see that team's opportunities, not other teams'. The MCP server never decides what to show — Dataverse does."
>
> **Dev:** "So if we later wanted a back-office agent that sees everything, we'd need a separate MCP server?"
>
> **Domain expert:** "Right. Same code path, different AAD app, different security-role assignments. We don't give the MCP server broad rights and then filter in code — that would defeat RLS."

## Flagged ambiguities

- **"agent"** was used to mean (a) the UI chat frontend, (b) the agent runtime (LLM + tool loop), or (c) the MCP tools. Resolved: "agent" = **reference agent** = the agent runtime; UI is separate and out of scope; tools live in the **MCP server**.
- **"secret"** was used to mean both the AAD app `client_secret` and tenant-specific values like `DATAVERSE_URL`. Resolved: `DATAVERSE_URL` is configuration (not sensitive in the cryptographic sense); `client_secret` is eliminated entirely via **WIF**.
- **"skill"** can mean (a) Claude Code / agent-side skill bundle (this project's `skill/`) or (b) a generic Azure term. Resolved: in this repo, "skill" always means the file bundle shipped to agents.
- **"environment"** is overloaded across (a) dev/staging/prod, (b) Azure Global vs Azure China, (c) Dataverse environments within a tenant. Resolved: we use **`CLOUD_ENV`** for (b) and qualify the others explicitly ("deployment environment", "Dataverse environment").
- **"orchestrator"** can mean (a) the LLM + tool loop inside a single agent or (b) a layer above multiple agents that composes them. Resolved: in this repo, "agent" covers (a) internally and we do not call it an orchestrator; "**orchestrator**" is reserved exclusively for meaning (b), the multi-agent composition layer, which is out of current scope but anticipated by the decoupling invariant.
