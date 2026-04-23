# Prompts as file-based module inside the reference agent

Agent-behavior prompts — system prompt, safety rules, few-shot examples, error-recovery phrasing — change frequently, are often authored by non-engineers (PM, sales ops, compliance), and may need per-LLM-provider variants. They live as Markdown files in `src/agent/prompts/` and are loaded at runtime, not embedded as Python string literals. The module is scoped to the reference agent: prompts are tightly coupled to a specific agent's orchestration loop and tool-call dialect, so they do not cross-cut and do not become a top-level deliverable.

## Considered Options

- **Inline Python strings** — simplest, but blocks non-engineer editing, defeats prompt-eval workflows, and makes localisation awkward.
- **Prompt variants inside each LLM provider class** — keeps providers self-contained, but fragments prompt content across Python files and loses the editorial goal. Rejected.
- **Top-level `prompts/` deliverable, parallel to skill / MCP / agent** — rejected. Agent-behavior prompts depend on the reference agent's tool-call format and orchestration assumptions; they do not stand alone for external agents.
- **Templating engine (Jinja, Handlebars)** — rejected. Introducing code into prompts defeats the editorial-access goal; simple `{variable}` string substitution is the permitted ceiling.

## Consequences

- `src/agent/prompts/` holds the full prompt surface; a small `prompts/README.md` documents editing conventions so non-engineers can contribute.
- Prompt files may contain only simple `{variable}` substitution — no conditionals, loops, or templating logic. If logic is needed, it belongs in the agent's Python code, not in the prompt.
- Tool descriptions live in the MCP server's tool schema, surfaced to the LLM via `list_tools`. They MUST NOT be duplicated in the system prompt — duplication guarantees they drift apart.
- Per-provider variants (if needed) live in `prompts/providers/*.md` and are appended to the base prompt at runtime. LLM provider classes (see [ADR 0005](./0005-llm-provider-abstraction.md)) stay prompt-free.
- Prompt changes can ship on a separate review track from code changes; CODEOWNERS rules can enforce non-engineer review on the `prompts/` path later if desired.
- Prompt behaviour is still not portable across LLM providers — see [ADR 0005](./0005-llm-provider-abstraction.md). Swapping providers requires re-testing prompts, even though the files are cleanly separated.
