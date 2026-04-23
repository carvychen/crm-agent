# Reference agent calls MCP over HTTP, not in-process

The reference agent and external agents (VS Code, Claude Desktop, Copilot Studio, a customer-built agent, or a future multi-agent orchestrator) are all first-class consumers of the MCP server. To keep a single HTTP + OBO code path that every consumer exercises — rather than an in-process shortcut only the reference agent would take — the reference agent calls the MCP server over HTTP+SSE even though both components deploy to the same Function App. The reference agent's integration tests then continuously validate the same transport any external agent will use.

## Considered Options

- **In-process import** (reference agent imports MCP tool functions directly) — rejected. Creates a dogfooding gap: bugs in the HTTP transport would only surface when an external agent hits them, after delivery, in an environment we cannot debug.
- **Dual stack** (in-process for reference agent, HTTP exposed for external) — rejected. Doubles the maintenance surface and makes "is the HTTP path working?" a question the reference agent's tests can't answer.
- **Separate Function Apps for agent and MCP server** — considered. Cleaner lifecycle separation, but adds a deployment artifact and a set of Bicep resources for no behavioural benefit at this scale. A later split is a mechanical refactor.

## Consequences

- ~20ms extra latency per tool call (localhost HTTP round trip). Negligible at expected volume.
- Integration tests on the reference agent exercise the exact HTTP + OBO path external agents use — "works for reference agent" implies "works for external agents".
- Reference-agent deployment is independently toggleable via `ENABLE_REFERENCE_AGENT` (default true). Customers running their own agent can deploy just the MCP server endpoint without any code change; this is a deployment option, not a signal that the reference agent is expendable.
- Agent and MCP endpoints share one Function App's config, monitoring, and deployment lifecycle. Splitting later remains a mechanical refactor, not an architectural one.
- **AF makes this free, not expensive.** The reference agent uses `agent_framework.MCPStreamableHTTPTool` (ADR 0005), which opens a standard Streamable HTTP MCP session against `MCP_SERVER_URL` for every tool invocation. The HTTP hop is a library-provided guarantee, not our own wiring — the invariant ("same transport as any external MCP client") is satisfied by AF's tool, not by our own connection code.
- **Deployment note: `MCP_SERVER_URL` must be reachable in-process.** Because `AsgiFunctionApp` invokes the ASGI app directly (no local port binding), the reference agent must point at the Function App's public URL (or an internal / Private Endpoint equivalent). A future optimisation — using `httpx.ASGITransport` to loopback through the same ASGI app in-process, still over the HTTP protocol — is compatible with the spirit of this ADR (exercises the Streamable HTTP code path) but intentionally deferred so the walking-skeleton path matches what an external consumer sees.
