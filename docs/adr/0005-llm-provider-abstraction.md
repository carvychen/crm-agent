# LLM provider abstraction

The production LLM is not yet chosen — it may be Azure OpenAI China, a Lenovo-approved domestic model (Qwen, DeepSeek, Baichuan, etc.), or a Foundry-hosted option — and it will almost certainly differ from what we use during development. The reference agent uses a minimal `LLMClient` interface, and concrete providers (`azure-openai-global`, `azure-openai-cn`, `foundry`, `custom`) are selected at runtime via the `LLM_PROVIDER` environment variable. Adding a new provider means implementing one class, not touching the agent's tool-loop code.

## Considered Options

- **Hardcode Azure OpenAI SDK directly** — rejected. Would require a rewrite when Lenovo picks a non-OpenAI model.
- **Use a third-party aggregator (LiteLLM, OpenRouter)** — rejected. Introduces a dependency that may not be approved for Lenovo's CN environment; also blurs the interface boundary we want to own.
- **Full LangChain / Semantic Kernel abstraction** — rejected as overkill. We need chat completion + tool calling, not the whole agent framework surface.

## Consequences

- The interface covers only `chat_completion` (with tool calling support). Provider-specific capabilities — vision inputs, streaming token logprobs, fine-tuning — are NOT abstracted. Features needed later require extending the interface deliberately, not sneaking in via a provider.
- Provider-specific quirks (token limit differences, tool-call JSON dialects, rate-limit headers) are normalised inside each provider implementation, not leaked to the agent's tool-loop code.
- **Prompt behaviour is not portable across providers.** The reference agent's system prompts are tuned against a specific LLM; swapping providers requires re-tuning and re-testing. We document the tested provider in `README.md` and mark others as "bring your own prompts".
- The abstraction adds ~100 lines of code and one indirection — a deliberate cost paid against a high-probability event (provider swap at handover).
