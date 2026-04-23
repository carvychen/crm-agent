# LLM provider abstraction (via Microsoft Agent Framework)

The production LLM is not yet chosen — it may be Azure OpenAI China, a Lenovo-approved domestic model (Qwen, DeepSeek, Baichuan, etc.), or a Foundry-hosted option — and it will almost certainly differ from what we use during development. The reference agent therefore runs on **Microsoft Agent Framework** (`agent_framework`), which already provides a `SupportsChatGetResponse` abstraction with concrete implementations for Azure AI Foundry, Azure OpenAI, Anthropic, Bedrock, Ollama, and a plug-in surface for custom providers. Adding a new provider means selecting (or implementing) one chat client and passing it to `agent_framework.Agent(client=...)` — the agent's tool-loop code does not change.

## History

This ADR was **revised during the Slice 2 HITL review**. The original version rejected "full LangChain / Semantic Kernel abstractions as overkill" and proposed a bespoke `LLMClient` ABC with concrete providers (`azure-openai-global`, `azure-openai-cn`, `foundry`, `custom`). That version was implemented in Slice 2 v1 and then discarded: the bespoke runtime lacked the production-grade features Invariant 2 requires (multi-turn session memory, sliding-window context compaction, rate-limit retry middleware, destructive-operation approval flow). Re-implementing those features in a custom runtime duplicates what Microsoft Agent Framework already ships.

The Slice 2 v1 PR (#14) was closed without merge. Slice 2 v2 adopts MS AF.

## Considered Options

- **Original proposal — bespoke `LLMClient` ABC** — rejected on review. Implementing it correctly means re-building `agent_framework`'s orchestration layer; our cost savings on "one extra dependency" were dwarfed by the implementation gap the demo's feature set would later force us to close.
- **Hardcode Azure OpenAI SDK directly** — rejected. Would require a rewrite when Lenovo picks a non-OpenAI model.
- **Third-party aggregator (LiteLLM, OpenRouter)** — rejected. Introduces a dependency that may not be approved for Lenovo's China environment; also blurs the interface boundary we want to own.
- **Full LangChain / Semantic Kernel abstraction** — rejected as overkill for the specific prompt-eval and middleware story we need.

## Consequences

- `agent_framework` and its provider packages (`agent_framework-foundry` to start; `agent_framework-azure-ai` / Anthropic / Bedrock / Ollama / `agent_framework-copilotstudio` available on demand) become pinned prod deps.
- Provider selection at deployment is driven by `LLM_PROVIDER` (Slice 2 ships `foundry`; Slice 6 widens the set by instantiating the corresponding AF chat client class). `function_app.py` is the single place this dispatch happens.
- **MCP-server layer independence is preserved**: AF ships `MCPStreamableHTTPTool`, which speaks the standard MCP Streamable HTTP protocol. The MCP server (Slice 1) is consumed by AF the same way any external MCP-compliant client would — no AF-specific coupling leaks into `src/mcp_server.py`, `src/auth.py`, or `src/dataverse_client.py`. The skill bundle and future orchestrator remain free to consume the MCP server without adopting AF.
- **Prompt behaviour is not portable across providers.** The reference agent's system prompts are tuned against a specific LLM; swapping providers requires re-tuning and re-testing. We document the tested provider in `README.md` and mark others as "bring your own prompts".
- **Production-grade features come for free.** `agent_framework.Agent` provides session memory (`create_session()`), sliding-window compaction (`SlidingWindowStrategy`), middleware layers (function / chat / agent), approval flows for destructive tool calls, and usage telemetry. Slices 3 and beyond wire these in as needed rather than reinventing them.
- **Per-request user identity** is carried by a `ContextVar` read by `MCPStreamableHTTPTool.header_provider`. The `/api/chat` route sets the context before `Agent.run(..., stream=True)` and resets on exit; concurrent callers are isolated.
