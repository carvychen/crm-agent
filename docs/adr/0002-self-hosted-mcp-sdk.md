# Self-hosted MCP SDK over Functions MCP extension

The Azure Functions MCP extension is in preview in global Azure and its availability window in Azure China (21Vianet) is not guaranteed for this project's production ship date; relying on it would block delivery to Lenovo. We implement the MCP server using the official Python MCP SDK mounted on a standard HTTP trigger, which depends only on GA Azure Functions capabilities and works identically in Global and China clouds.

## Considered Options

- **Azure Functions MCP extension** — cleaner bindings model, but preview-only. Rejected because invariant 3 (cloud-neutral, no preview features) and the China availability risk.
- **Container Apps hosting MCP SDK** — also valid, but the customer has expressed a preference for Function App. Rejected on customer preference, not technical merit.
- **App Service Web App** — heavier than needed for 100/day, no per-request billing. Rejected on cost profile.

## Consequences

- We own ~50 lines of transport plumbing (HTTP+SSE wiring to the MCP SDK server instance) — this is standard SDK usage, not a custom protocol.
- The MCP server is portable: the same code runs on Container Apps, App Service, or any Python HTTP host if Lenovo later moves off Functions. No Functions-specific coupling inside the MCP layer.
- Cold start on Consumption plan applies to both the agent and MCP endpoints since they share a Function App. At 100/day this is acceptable; revisit if usage spikes.
- **Client-side is independent.** The reference agent consumes this server through `agent_framework.MCPStreamableHTTPTool` (the same Python MCP SDK on the client side, ADR 0005). The server-side SDK choice is orthogonal to whichever client (AF's MCP tool, Claude Desktop, VS Code Copilot MCP, Copilot Studio, or a custom client) an external agent chooses — they all speak the same Streamable HTTP protocol.
