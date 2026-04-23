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

import httpx
from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient

from agent.prompts.loader import PromptLoader


# Set per-request by `/api/chat` before invoking the Agent. Read at tool-call
# time by the header provider on the MCPStreamableHTTPTool so the MCP server
# sees the inbound user JWT and OBO preserves end-user identity (ADR 0001).
current_user_jwt: ContextVar[str] = ContextVar("current_user_jwt")


def bearer_header_provider(_kwargs: dict[str, Any]) -> dict[str, str]:
    """Return the Authorization header for the current in-flight request.

    Publicly exposed for testability: reads `current_user_jwt` from the ambient
    ContextVar at call time so each request forwards its own user JWT without
    risk of bleed across concurrent callers.
    """
    return {"Authorization": f"Bearer {current_user_jwt.get()}"}


async def _bearer_request_hook(request: httpx.Request) -> None:
    """httpx request-event hook that attaches `Authorization: Bearer <jwt>` to
    every outbound MCP request.

    We cannot rely on AF's own `header_provider` mechanism because it only fires
    inside `MCPStreamableHTTPTool.call_tool` — the initial MCP `initialize`
    handshake POST therefore goes out unauthenticated and is rejected by the
    MCP server with 401. Attaching the bearer at the httpx layer covers every
    request in the session (initialize, tools/list, call_tool, ...).
    """
    try:
        request.headers["Authorization"] = f"Bearer {current_user_jwt.get()}"
    except LookupError:
        # Called outside a request context (e.g. startup probes). Leave the
        # request unauthorised; the server will 401 and surface the error.
        return


def build_agent(
    *,
    project_endpoint: str,
    model: str,
    mcp_url: str,
    prompts: PromptLoader,
    credential: Any,
    current_date: str | None = None,
    mcp_http_client: httpx.AsyncClient | None = None,
) -> Agent:
    """Compose a ready-to-run reference agent.

    The returned Agent owns its FoundryChatClient and its MCP tool connection;
    it is intended to live for the Functions host's lifetime.

    `mcp_http_client` is an advanced seam used by live-integration tests to
    route the MCP traffic through an in-process ASGI transport; production
    callers leave it None so AF creates a default httpx client internally.
    Passing the client at construction time is load-bearing — AF attaches its
    `header_provider` injection hook to whichever client is set here, so
    swapping `_httpx_client` post hoc silently drops the Authorization header.
    """
    rendered_date = current_date or date.today().isoformat()

    client = FoundryChatClient(
        project_endpoint=project_endpoint,
        model=model,
        credential=credential,
    )

    # We own the httpx client so we can attach the bearer hook to every
    # request (not just call_tool). AF's built-in `header_provider` only fires
    # during tool invocations and misses the MCP `initialize` handshake, which
    # our server rejects with 401.
    if mcp_http_client is None:
        mcp_http_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=300.0),
        )
    mcp_http_client.event_hooks.setdefault("request", []).append(_bearer_request_hook)

    mcp_tool = MCPStreamableHTTPTool(
        name="crm-mcp",
        url=mcp_url,
        http_client=mcp_http_client,
        # Our MCP server only serves tools (ADR 0002); it does not implement
        # the optional `prompts/*` capability. Telling AF to skip prompt
        # discovery avoids a fatal "Method not found" during `connect()`.
        load_prompts=False,
        # Slice 3: destructive Dataverse writes are gated behind a user
        # confirmation. AF surfaces a FunctionApprovalRequestContent in the
        # response stream; the UI collects the user's yes/no and sends back a
        # FunctionApprovalResponseContent on the next turn.
        approval_mode={"always_require_approval": ["delete_opportunity"]},
    )

    return Agent(
        client=client,
        instructions=prompts.render(current_date=rendered_date),
        tools=[mcp_tool],
    )
