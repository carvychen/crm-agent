"""Reference agent builder — composes an `agent_framework.Agent` from its parts.

ADR 0005 (amended) selects Microsoft Agent Framework as the orchestration
runtime for the reference agent. The production-grade features Invariant 2
requires (session memory, sliding-window compaction, middleware, approval
flows) live inside AF; we compose provider + tool + prompt into an Agent
instance and stream its output from `/api/chat`.

Invariant 1 stays intact: MCP is consumed through AF's own
`MCPStreamableHTTPTool`, which speaks the standard protocol. The MCP server
module (`src/mcp_server.py`) has no AF-specific assumptions — any external
MCP-compliant client can use it.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import date
from typing import Any

from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient

from agent.prompts.loader import PromptLoader


# Set per-request by `/api/chat` before invoking the Agent. Read at tool-call
# time by the header provider on the MCPStreamableHTTPTool so the MCP server
# sees the inbound user JWT and OBO preserves end-user identity (ADR 0001).
current_user_jwt: ContextVar[str] = ContextVar("current_user_jwt")


def bearer_header_provider(_kwargs: dict[str, Any]) -> dict[str, str]:
    """Header provider used by the MCP tool. Publicly exposed for testability:
    reads `current_user_jwt` from the ambient ContextVar at call time so each
    request forwards its own user JWT without risk of bleed across concurrent
    callers.
    """
    return {"Authorization": f"Bearer {current_user_jwt.get()}"}


def build_agent(
    *,
    project_endpoint: str,
    model: str,
    mcp_url: str,
    prompts: PromptLoader,
    credential: Any,
    current_date: str | None = None,
) -> Agent:
    """Compose a ready-to-run reference agent.

    The returned Agent owns its FoundryChatClient and its MCP tool connection;
    it is intended to live for the Functions host's lifetime.
    """
    rendered_date = current_date or date.today().isoformat()

    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=credential,
    )
    mcp_tool = MCPStreamableHTTPTool(
        name="crm-mcp",
        url=mcp_url,
        header_provider=bearer_header_provider,
    )

    return Agent(
        client=client,
        instructions=prompts.render(current_date=rendered_date),
        tools=[mcp_tool],
    )
